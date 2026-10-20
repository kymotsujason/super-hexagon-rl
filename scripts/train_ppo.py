import argparse
import collections
import csv
import sys
import time
import traceback
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "env"))

import numpy as np
import torch
import torch.multiprocessing as tmp
import torch.nn as nn

from shxenv import LEVEL_COUNT
from shxgame import OBS_SIZE

N_ACTIONS = 3


def layer(n_in, n_out, std=np.sqrt(2)):
    linear = nn.Linear(n_in, n_out)
    nn.init.orthogonal_(linear.weight, std)
    nn.init.constant_(linear.bias, 0.0)
    return linear


# A network built for a shorter observation reads only its first obs_size values, so checkpoints
# from before a feature was appended still play.
class Agent(nn.Module):
    def __init__(self, obs_size, n_actions, hidden):
        super().__init__()
        self.obs_size = obs_size
        self.critic = nn.Sequential(
            layer(obs_size, hidden), nn.Tanh(),
            layer(hidden, hidden), nn.Tanh(),
            layer(hidden, 1, std=1.0),
        )
        self.actor = nn.Sequential(
            layer(obs_size, hidden), nn.Tanh(),
            layer(hidden, hidden), nn.Tanh(),
            layer(hidden, n_actions, std=0.01),
        )

    def logits(self, x):
        return self.actor(x[..., :self.obs_size])

    def value(self, x):
        return self.critic(x[..., :self.obs_size]).squeeze(-1)

    def act(self, x, action=None):
        dist = torch.distributions.Categorical(logits=self.logits(x))
        if action is None:
            action = dist.sample()
        return action, dist.log_prob(action), dist.entropy(), self.value(x)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--run-name", default="ppo")
    p.add_argument("--levels", type=int, nargs="+", default=list(range(LEVEL_COUNT)))
    p.add_argument("--backend", choices=["game", "sim"], default="game",
                   help="real games through the hook, or the C simulator in this process")
    p.add_argument("--sims", type=int, default=1024, help="simulators, with --backend sim")
    p.add_argument("--sim-threads", type=int, default=8, help="threads stepping simulators, with --backend sim")
    p.add_argument("--actors", type=int, default=6)
    p.add_argument("--games-per-actor", type=int, default=16)
    p.add_argument("--frame-skip", type=int, default=2)
    p.add_argument("--max-minutes", type=float, default=20.0)
    p.add_argument("--total-steps", type=int, default=10 ** 10)
    p.add_argument("--num-steps", type=int, default=256)
    p.add_argument("--gamma", type=float, default=0.995)
    p.add_argument("--gae-lambda", type=float, default=0.95)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--epochs", type=int, default=4)
    p.add_argument("--minibatches", type=int, default=4)
    p.add_argument("--clip", type=float, default=0.2)
    p.add_argument("--ent-coef", type=float, default=0.01)
    p.add_argument("--vf-coef", type=float, default=0.5)
    p.add_argument("--max-grad-norm", type=float, default=0.5)
    p.add_argument("--hidden", type=int, default=256)
    p.add_argument("--max-episode-seconds", type=float, default=600.0)
    p.add_argument("--restart-prob", type=float, default=0.25)
    p.add_argument("--level-target-seconds", type=float, default=120.0)
    p.add_argument("--reversal-cost", type=float, default=0.0,
                   help="reward cost when the action flips straight between left and right")
    p.add_argument("--press-cost", type=float, default=0.0,
                   help="reward cost each time a direction press starts, so holds are free and taps aren't")
    p.add_argument("--press-cost-end", type=float, default=None,
                   help="anneal the press cost linearly to this by the end of the run")
    p.add_argument("--anneal-start", type=float, default=0.0,
                   help="fraction of the run to hold the press cost before annealing starts")
    p.add_argument("--init-model", default=None, help="checkpoint to start from")
    p.add_argument("--add-hold", action="store_true",
                   help="give a checkpoint from before the hold feature an input for it, starting at zero weight")
    p.add_argument("--ema-decay", type=float, default=0.999,
                   help="decay per update of the running weight average saved beside each snapshot")
    p.add_argument("--swa-start-minutes", type=float, default=None,
                   help="also keep a uniform running average of the weights from this minute on")
    p.add_argument("--swa-start-progress", type=float, default=None,
                   help="the same, from this fraction of the run on")
    p.add_argument("--snapshot-progress", type=float, default=0.0,
                   help="keep a checkpoint each time the run gets this much further, named by percent done")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--checkpoint-every", type=int, default=25)
    p.add_argument("--snapshot-minutes", type=float, default=0.0,
                   help="also keep a separate checkpoint this often, for choosing the best one later")
    return p.parse_args()


def gae(rewards, dones, values, next_values, gamma, lam):
    steps = rewards.shape[0]
    adv = np.zeros_like(rewards)
    last = np.zeros_like(next_values)
    for t in reversed(range(steps)):
        following = next_values if t == steps - 1 else values[t + 1]
        not_done = 1.0 - dones[t]
        delta = rewards[t] + gamma * following * not_done - values[t]
        last = delta + gamma * lam * not_done * last
        adv[t] = last
    return adv


def save(path, agent, args, update, global_step, state=None):
    torch.save({"model": state if state is not None else agent.state_dict(), "args": vars(args),
                "obs_size": agent.obs_size, "update": update, "global_step": global_step}, path)


# Running averages of the weights over training. The EMA follows the recent updates, while the
# uniform average weighs every update since it started the same.
class WeightAverages:
    def __init__(self, agent, ema_decay):
        self.agent = agent
        self.ema_decay = ema_decay
        self.ema = {k: v.detach().clone() for k, v in agent.state_dict().items()}
        self.swa = None
        self.swa_count = 0

    def update(self, include_swa):
        current = self.agent.state_dict()
        with torch.no_grad():
            for key, value in current.items():
                self.ema[key].lerp_(value, 1.0 - self.ema_decay)
            if include_swa:
                if self.swa is None:
                    self.swa = {k: v.detach().clone() for k, v in current.items()}
                else:
                    for key, value in current.items():
                        self.swa[key] += (value - self.swa[key]) / (self.swa_count + 1)
                self.swa_count += 1


# How far through the run this is, by time or by steps, whichever is further. With a step budget
# and a generous time limit, the schedules follow the steps, so a run slowed by a CPU quota gets
# the same training, only later.
def progress_of(args, elapsed, global_step):
    return max(elapsed / (args.max_minutes * 60), global_step / args.total_steps)


def press_cost_at(args, frac_done):
    if args.press_cost_end is None or frac_done <= args.anneal_start:
        return args.press_cost
    t = min((frac_done - args.anneal_start) / max(1.0 - args.anneal_start, 1e-9), 1.0)
    return args.press_cost + t * (args.press_cost_end - args.press_cost)


# Builds the network, from a checkpoint when given. A checkpoint from before the hold feature keeps
# its 44 inputs unless add_hold widens its first layers, with zero weights on the new input so
# it still plays the same.
def build_agent(args, device):
    if not args.init_model:
        return Agent(OBS_SIZE, N_ACTIONS, args.hidden).to(device)
    checkpoint = torch.load(args.init_model, map_location="cpu", weights_only=True)
    old_size = checkpoint.get("obs_size", OBS_SIZE)
    agent = Agent(old_size, N_ACTIONS, args.hidden)
    agent.load_state_dict(checkpoint["model"])
    if args.add_hold and old_size < OBS_SIZE:
        wide = Agent(OBS_SIZE, N_ACTIONS, args.hidden)
        state = agent.state_dict()
        for key in ("actor.0.weight", "critic.0.weight"):
            old = state[key]
            state[key] = torch.cat([old, torch.zeros(old.shape[0], OBS_SIZE - old_size)], dim=1)
        wide.load_state_dict(state)
        x = torch.randn(4096, OBS_SIZE)
        with torch.no_grad():
            diff = (wide.logits(x) - agent.logits(x)).abs().max().item()
        if diff > 1e-5:
            raise SystemExit(f"adding the hold input changed the outputs by {diff}")
        print(f"added the hold input to {args.init_model}, largest output difference {diff:.1e}", flush=True)
        agent = wide
    return agent.to(device)


# One actor drives its own group of games with a CPU copy of the policy, and writes its rollout
# into its columns of the shared buffers. Several actors overlap the time each game takes to wake.
def actor_main(index, args, columns, shared_agent, buffers, commands, results):
    try:
        from shxvec import ShxVecEnv

        torch.set_num_threads(1)
        torch.manual_seed(args.seed * 100 + index)
        run_dir = PROJECT_DIR / "runs" / "train" / args.run_name / f"actor{index}"
        envs = ShxVecEnv(args.games_per_actor, run_dir, levels=tuple(args.levels), frame_skip=args.frame_skip,
                         seed=args.seed * 100 + index, max_episode_seconds=args.max_episode_seconds,
                         restart_prob=args.restart_prob, level_target_seconds=args.level_target_seconds)
        local = Agent(shared_agent.obs_size, N_ACTIONS, args.hidden)
        obs_buf, act_buf, logp_buf, rew_buf, done_buf, val_buf, next_val = buffers
        lo, hi = columns
        next_obs = torch.from_numpy(envs.reset())
        results.put(("ready", index, None))

        while True:
            command, averages = commands.get()
            if command == "stop":
                break
            envs.sampler.set_averages(averages)
            local.load_state_dict(shared_agent.state_dict())
            finished_all = []
            for t in range(args.num_steps):
                obs_buf[t, lo:hi] = next_obs
                with torch.no_grad():
                    action, logp, _, value = local.act(next_obs)
                act_buf[t, lo:hi] = action
                logp_buf[t, lo:hi] = logp
                val_buf[t, lo:hi] = value
                raw_obs, reward, term, trunc, final_obs, finished = envs.step(action.numpy())
                # a time limit isn't a death, so bootstrap from the value of where the run was cut
                if final_obs:
                    idx = list(final_obs)
                    final = torch.from_numpy(np.stack([final_obs[i] for i in idx]))
                    with torch.no_grad():
                        reward[idx] += args.gamma * local.value(final).numpy()
                rew_buf[t, lo:hi] = torch.from_numpy(reward)
                done_buf[t, lo:hi] = torch.from_numpy((term | trunc).astype(np.float32))
                finished_all.extend(finished)
                next_obs = torch.from_numpy(raw_obs)
            with torch.no_grad():
                next_val[lo:hi] = local.value(next_obs)
            results.put(("done", index, (finished_all, envs.endings)))
        envs.close()
    except BaseException:
        results.put(("error", index, traceback.format_exc()))


# One process: the simulators step on threads, actions come from one GPU batch per step, and the
# update follows each rollout. The simulators are fast enough that this beats spreading out.
def train_on_simulator(args, run_dir, device, agent, optimizer):
    from shxvec import ShxSimVecEnv

    envs = ShxSimVecEnv(args.sims, run_dir=run_dir, threads=args.sim_threads, levels=tuple(args.levels),
                        frame_skip=args.frame_skip, seed=args.seed, max_episode_seconds=args.max_episode_seconds,
                        restart_prob=args.restart_prob, level_target_seconds=args.level_target_seconds,
                        reversal_cost=args.reversal_cost, press_cost=args.press_cost)
    n, e = args.num_steps, args.sims
    batch = n * e
    minibatch = batch // args.minibatches
    obs_buf = torch.zeros((n, e, OBS_SIZE), device=device)
    act_buf = torch.zeros((n, e), dtype=torch.long, device=device)
    logp_buf = torch.zeros((n, e), device=device)
    val_buf = torch.zeros((n, e), device=device)
    rew_np = np.zeros((n, e), dtype=np.float32)
    done_np = np.zeros((n, e), dtype=np.float32)

    log_file = open(run_dir / "log.csv", "w", newline="")
    log = csv.writer(log_file)
    level_columns = [f"mean_l{level}" for level in range(LEVEL_COUNT)]
    log.writerow(["update", "global_step", "sps", "episodes", "restarts", "endings", *level_columns,
                  "pg_loss", "v_loss", "entropy", "approx_kl", "rollout_s", "update_s", "flips_per_s",
                  "presses_per_s", "press_cost"])
    recent = {level: collections.deque(maxlen=200) for level in range(LEVEL_COUNT)}
    episodes = restarts = global_step = update = 0
    rollout_time = update_time = 0.0
    next_snapshot = args.snapshot_minutes * 60 if args.snapshot_minutes > 0 else None
    next_progress = args.snapshot_progress if args.snapshot_progress > 0 else None
    averages = WeightAverages(agent, args.ema_decay)
    try:
        next_obs = torch.from_numpy(envs.reset()).to(device)
        start = time.perf_counter()
        print(f"run {run_dir}, {e} simulators on {args.sim_threads} threads, levels {args.levels}, "
              f"batch {batch}, training on {device}", flush=True)
        while global_step < args.total_steps and time.perf_counter() - start < args.max_minutes * 60:
            update += 1
            frac = 1.0 - progress_of(args, time.perf_counter() - start, global_step)
            optimizer.param_groups[0]["lr"] = max(frac, 0.05) * args.lr
            envs.press_cost = press_cost_at(args, 1.0 - frac)

            rollout_start = time.perf_counter()
            for t in range(n):
                obs_buf[t] = next_obs
                with torch.no_grad():
                    action, logp, _, value = agent.act(next_obs)
                act_buf[t], logp_buf[t], val_buf[t] = action, logp, value
                raw_obs, reward, term, trunc, final_obs, finished = envs.step(action.cpu().numpy())
                if final_obs:
                    idx = list(final_obs)
                    final = torch.from_numpy(np.stack([final_obs[i] for i in idx])).to(device)
                    with torch.no_grad():
                        reward[idx] += args.gamma * agent.value(final).cpu().numpy()
                for info in finished:
                    episodes += 1
                    if info["fresh"]:
                        recent[info["level"]].append(info["seconds"])
                    else:
                        restarts += 1
                rew_np[t] = reward
                done_np[t] = (term | trunc)
                next_obs = torch.from_numpy(raw_obs).to(device)
            global_step += batch
            update_start = time.perf_counter()
            rollout_time += update_start - rollout_start

            with torch.no_grad():
                next_value = agent.value(next_obs).cpu().numpy()
            adv_np = gae(rew_np, done_np, val_buf.cpu().numpy(), next_value, args.gamma, args.gae_lambda)
            b_adv = torch.from_numpy(adv_np).reshape(batch).to(device)
            b_ret = b_adv + val_buf.reshape(batch)
            b_obs = obs_buf.reshape(batch, OBS_SIZE)
            b_act = act_buf.reshape(batch)
            b_logp = logp_buf.reshape(batch)
            for _ in range(args.epochs):
                perm = torch.randperm(batch, device=device)
                for s in range(0, batch, minibatch):
                    mb = perm[s:s + minibatch]
                    _, new_logp, entropy, new_value = agent.act(b_obs[mb], b_act[mb])
                    ratio = (new_logp - b_logp[mb]).exp()
                    mb_adv = (b_adv[mb] - b_adv[mb].mean()) / (b_adv[mb].std() + 1e-8)
                    pg_loss = torch.max(-mb_adv * ratio,
                                        -mb_adv * ratio.clamp(1 - args.clip, 1 + args.clip)).mean()
                    v_loss = 0.5 * (new_value - b_ret[mb]).pow(2).mean()
                    ent = entropy.mean()
                    loss = pg_loss + args.vf_coef * v_loss - args.ent_coef * ent
                    optimizer.zero_grad()
                    loss.backward()
                    nn.utils.clip_grad_norm_(agent.parameters(), args.max_grad_norm)
                    optimizer.step()
                    with torch.no_grad():
                        approx_kl = ((ratio - 1) - (new_logp - b_logp[mb])).mean().item()
            update_time += time.perf_counter() - update_start

            elapsed = time.perf_counter() - start
            done = progress_of(args, elapsed, global_step)
            averages.update((args.swa_start_minutes is not None and elapsed >= args.swa_start_minutes * 60)
                            or (args.swa_start_progress is not None and done >= args.swa_start_progress))
            sps = global_step / elapsed
            means = [float(np.mean(recent[level])) if recent[level] else 0.0 for level in range(LEVEL_COUNT)]
            # sampled actions during training, so this runs higher than greedy play
            flips_per_s = envs.flips / max(envs.decisions, 1) * 60 / args.frame_skip
            presses_per_s = envs.presses / max(envs.decisions, 1) * 60 / args.frame_skip
            envs.flips = envs.presses = envs.decisions = 0
            log.writerow([update, global_step, round(sps), episodes, restarts, envs.endings,
                          *[round(m, 2) for m in means], round(pg_loss.item(), 4), round(v_loss.item(), 4),
                          round(ent.item(), 4), round(approx_kl, 5), round(rollout_time, 1), round(update_time, 1),
                          round(flips_per_s, 3), round(presses_per_s, 3), round(envs.press_cost, 6)])
            log_file.flush()
            if update % 10 == 0 or update == 1:
                level_text = " ".join(f"{m:5.1f}" for m in means)
                print(f"update {update:4} step {global_step:13,} sps {sps:9,.0f} episodes {episodes:7} "
                      f"restarts {restarts:6} endings {envs.endings} survival by level [{level_text}] "
                      f"entropy {ent.item():.3f} flips {flips_per_s:.2f}/s presses {presses_per_s:.2f}/s "
                      f"press cost {envs.press_cost:.5f} rollout {rollout_time:.0f}s "
                      f"update {update_time:.0f}s", flush=True)
            if update % args.checkpoint_every == 0:
                save(run_dir / "model.pt", agent, args, update, global_step)
            if next_snapshot is not None and elapsed >= next_snapshot:
                minute = int(elapsed // 60)
                save(run_dir / f"model_{minute:03d}min.pt", agent, args, update, global_step)
                save(run_dir / f"ema_{minute:03d}min.pt", agent, args, update, global_step, averages.ema)
                if averages.swa is not None:
                    save(run_dir / f"swa_{minute:03d}min.pt", agent, args, update, global_step, averages.swa)
                next_snapshot += args.snapshot_minutes * 60
            if next_progress is not None and done >= next_progress - 1e-9 and done < 1.0:
                percent = int(round(next_progress * 100))
                save(run_dir / f"model_p{percent:03d}.pt", agent, args, update, global_step)
                save(run_dir / f"ema_p{percent:03d}.pt", agent, args, update, global_step, averages.ema)
                if averages.swa is not None:
                    save(run_dir / f"swa_p{percent:03d}.pt", agent, args, update, global_step, averages.swa)
                next_progress += args.snapshot_progress
        save(run_dir / "model.pt", agent, args, update, global_step)
        save(run_dir / "ema.pt", agent, args, update, global_step, averages.ema)
        if averages.swa is not None:
            save(run_dir / "swa.pt", agent, args, update, global_step, averages.swa)
            print(f"uniform average over the last {averages.swa_count} updates saved as swa.pt", flush=True)
        print(f"done: {global_step:,} steps in {(time.perf_counter() - start) / 60:.1f} min, "
              f"saved {run_dir / 'model.pt'}", flush=True)
    finally:
        log_file.close()
        envs.close()


def main():
    args = parse_args()
    run_dir = PROJECT_DIR / "runs" / "train" / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    torch.set_num_threads(2)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    agent = build_agent(args, device)
    # fused Adam launches far fewer kernels per step, and the update is launch bound at this size
    optimizer = torch.optim.Adam(agent.parameters(), lr=args.lr, eps=1e-5, fused=device.type == "cuda")
    if args.backend == "sim":
        train_on_simulator(args, run_dir, device, agent, optimizer)
        return

    shared_agent = Agent(agent.obs_size, N_ACTIONS, args.hidden)
    shared_agent.load_state_dict(agent.state_dict())
    shared_agent.share_memory()

    n = args.num_steps
    e = args.actors * args.games_per_actor
    buffers = [
        torch.zeros((n, e, OBS_SIZE)), torch.zeros((n, e), dtype=torch.long), torch.zeros((n, e)),
        torch.zeros((n, e)), torch.zeros((n, e)), torch.zeros((n, e)), torch.zeros(e),
    ]
    for buf in buffers:
        buf.share_memory_()
    obs_buf, act_buf, logp_buf, rew_buf, done_buf, val_buf, next_val = buffers

    ctx = tmp.get_context("spawn")
    results = ctx.Queue()
    command_queues = [ctx.Queue() for _ in range(args.actors)]
    actors = []
    for k in range(args.actors):
        columns = (k * args.games_per_actor, (k + 1) * args.games_per_actor)
        proc = ctx.Process(target=actor_main,
                           args=(k, args, columns, shared_agent, buffers, command_queues[k], results))
        proc.start()
        actors.append(proc)

    def collect(expected_kind):
        replies = []
        for _ in range(args.actors):
            kind, index, payload = results.get(timeout=600)
            if kind == "error":
                raise RuntimeError(f"actor {index} failed:\n{payload}")
            if kind != expected_kind:
                raise RuntimeError(f"actor {index} sent {kind}, expected {expected_kind}")
            replies.append((index, payload))
        return replies

    log_file = open(run_dir / "log.csv", "w", newline="")
    log = csv.writer(log_file)
    level_columns = [f"mean_l{level}" for level in range(LEVEL_COUNT)]
    log.writerow(["update", "global_step", "sps", "episodes", "restarts", "endings", *level_columns,
                  "pg_loss", "v_loss", "entropy", "approx_kl", "rollout_s", "update_s"])
    recent = {level: collections.deque(maxlen=100) for level in range(LEVEL_COUNT)}
    averages = {level: 0.0 for level in args.levels}
    endings = {}
    episodes = 0
    restarts = 0
    global_step = 0
    batch = n * e
    minibatch = batch // args.minibatches
    rollout_pending = False

    try:
        collect("ready")
        start = time.perf_counter()
        update = 0
        rollout_time = 0.0
        update_time = 0.0
        print(f"run {run_dir}, {args.actors} actors x {args.games_per_actor} games, levels {args.levels}, "
              f"training on {device}", flush=True)

        # actors always run one rollout ahead of the update, using the weights from the update
        # before, so the GPU update and the next rollout overlap
        def start_rollout():
            for queue in command_queues:
                queue.put(("collect", dict(averages)))

        start_rollout()
        rollout_pending = True
        while True:
            update += 1
            wait_start = time.perf_counter()
            for index, (finished, actor_endings) in collect("done"):
                endings[index] = actor_endings
                for info in finished:
                    episodes += 1
                    if info["fresh"]:
                        recent[info["level"]].append(info["seconds"])
                        level = info["level"]
                        if level in averages:
                            averages[level] += 0.05 * (info["seconds"] - averages[level])
                    else:
                        restarts += 1
            rollout_pending = False
            global_step += batch
            update_start = time.perf_counter()
            rollout_time += update_start - wait_start

            # take this rollout off the shared buffers before the actors start writing the next one
            b_obs = obs_buf.reshape(batch, OBS_SIZE).to(device)
            b_act = act_buf.reshape(batch).to(device)
            b_logp = logp_buf.reshape(batch).to(device)
            # GAE is a short sequential loop over small arrays, which numpy does faster than the GPU
            adv_np = gae(rew_buf.numpy(), done_buf.numpy(), val_buf.numpy(), next_val.numpy(),
                         args.gamma, args.gae_lambda)
            b_adv = torch.from_numpy(adv_np).reshape(batch).to(device)
            b_ret = torch.from_numpy(adv_np + val_buf.numpy()).reshape(batch).to(device)

            # the actors are idle here, so publishing weights can't race a copy
            shared_agent.load_state_dict(agent.state_dict())
            out_of_time = time.perf_counter() - start >= args.max_minutes * 60
            if not out_of_time and global_step < args.total_steps:
                start_rollout()
                rollout_pending = True

            frac = 1.0 - (time.perf_counter() - start) / (args.max_minutes * 60)
            optimizer.param_groups[0]["lr"] = max(frac, 0.05) * args.lr
            for _ in range(args.epochs):
                perm = torch.randperm(batch, device=device)
                for s in range(0, batch, minibatch):
                    mb = perm[s:s + minibatch]
                    _, new_logp, entropy, new_value = agent.act(b_obs[mb], b_act[mb])
                    ratio = (new_logp - b_logp[mb]).exp()
                    mb_adv = (b_adv[mb] - b_adv[mb].mean()) / (b_adv[mb].std() + 1e-8)
                    pg_loss = torch.max(-mb_adv * ratio,
                                        -mb_adv * ratio.clamp(1 - args.clip, 1 + args.clip)).mean()
                    v_loss = 0.5 * (new_value - b_ret[mb]).pow(2).mean()
                    ent = entropy.mean()
                    loss = pg_loss + args.vf_coef * v_loss - args.ent_coef * ent
                    optimizer.zero_grad()
                    loss.backward()
                    nn.utils.clip_grad_norm_(agent.parameters(), args.max_grad_norm)
                    optimizer.step()
                    with torch.no_grad():
                        approx_kl = ((ratio - 1) - (new_logp - b_logp[mb])).mean().item()
            update_time += time.perf_counter() - update_start

            elapsed = time.perf_counter() - start
            sps = global_step / elapsed
            means = [float(np.mean(recent[level])) if recent[level] else 0.0 for level in range(LEVEL_COUNT)]
            total_endings = sum(endings.values())
            log.writerow([update, global_step, round(sps), episodes, restarts, total_endings,
                          *[round(m, 2) for m in means], round(pg_loss.item(), 4), round(v_loss.item(), 4),
                          round(ent.item(), 4), round(approx_kl, 5), round(rollout_time, 1), round(update_time, 1)])
            log_file.flush()
            if update % 10 == 0 or update == 1:
                level_text = " ".join(f"{m:5.1f}" for m in means)
                print(f"update {update:4} step {global_step:11,} sps {sps:7,.0f} episodes {episodes:6} "
                      f"restarts {restarts:5} endings {total_endings} survival by level [{level_text}] "
                      f"entropy {ent.item():.3f} waiting {rollout_time:.0f}s update {update_time:.0f}s",
                      flush=True)
            if update % args.checkpoint_every == 0:
                save(run_dir / "model.pt", agent, args, update, global_step)
            if not rollout_pending:
                break

        save(run_dir / "model.pt", agent, args, update, global_step)
        print(f"done: {global_step:,} steps in {(time.perf_counter() - start) / 60:.1f} min, "
              f"saved {run_dir / 'model.pt'}", flush=True)
    finally:
        # a rollout still running has to finish before the actors read the stop command
        if rollout_pending:
            try:
                collect("done")
            except Exception:
                pass
        for queue in command_queues:
            queue.put(("stop", None))
        for proc in actors:
            proc.join(timeout=60)
            if proc.is_alive():
                proc.kill()
        log_file.close()


if __name__ == "__main__":
    main()
