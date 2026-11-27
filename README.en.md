# token-killer

Choose square packing, Four Realms Relay, Cap Set, Golomb rulers or the Hyperdimensional Recursive Simulator in the run console. A session defaults to one lane and ten minutes. Select a duration, start or stop, and track effective tokens per minute, the session total and time remaining. Select 40 lanes explicitly to try a high-concurrency workload.

Refresh **Shared plan allowance** for the remaining percentage and reset time of each available account window. Live requests require explicit ordinary-plan permission and positive allowance in every applicable window. If the reading is unavailable or a limit is reached, the runner stops sending new requests and retains existing receipts. Local puzzles and verification run offline.

A local challenge for people who want to find the fastest way to turn model tokens into verifiable game moves. Compare **effective tokens per minute over ten minutes** and record how many distinct valid candidates each strategy produced. Choose English or 中文 in the lab and either game. Original model replies and judge receipts keep their recorded language. [中文指南](README.md) · [Challenge and submission rules](CONTRIBUTING.md)

## Play locally

In **Client**, choose **OpenAI · ChatGPT** for automatic runs. Pick a game and a time limit, then optionally enter an effective-token target (for example, `100000`). The console shows progress, cumulative usage and the rate of completed receipts over the last 60 seconds. Reaching either limit ends dispatch. Replies already in flight can take the final count over the target.

For **Codex MCP**, **Claude Code** or **Google Antigravity**, configure the client once, choose square packing or relay, and create a game with a round limit. Copy its handoff into your client. The console updates as the judge receives each submission. A token target requires actual per-turn receipts from the client. If your client does not expose these, choose a round limit and follow judged rounds per minute. [Client setup guide](CLIENTS.md)

Extract the ZIP or clone the repository into a writable directory. Use Python 3.10 or newer. The offline games, SQLite saves and judges use the standard library. The launcher checks for 20 GiB of free space on the project and temporary volumes.

| Platform | From the project directory |
|---|---|
| Windows | Double-click `启动解题实验室.cmd`, or run `py -3 -B -X utf8 lab.py` |
| macOS or Linux | Run `sh start.sh --open` |
| Terminal without automatic browser launch | Run `python3 -B -X utf8 lab.py` and open the printed loopback URL on the same machine |

Choose **Square packing** to pack unit squares into a smaller square, or **Four Realms Relay** to cover more pairs of stations with a fixed set of lists. **Offline rehearsal** runs an offline comparison. **Project scores** shows locally verified records. Press Ctrl+C in the launching terminal to stop. Keep the game SQLite files and the `验收/` results directory when moving an installation.

## Add model play or MCP

Create a virtual environment (`py -3 -m venv .venv` on Windows or `python3 -m venv .venv` on Unix). Use that environment’s Python to run `-m pip install -r requirements.txt`. The launchers prefer that interpreter. For MCP, set the absolute interpreter and server paths in [the example configuration](mcp.config.example.toml); then call `game_status` from your client.

For a real model run, open the ChatGPT subscription settings in the lab, connect your supported plan, configure the application cap in the official settings and confirm it locally. Windows protects credentials with DPAPI. On macOS the game uses Keychain, and on Linux it requires a running Secret Service in your desktop session. Offline play works without connecting an account. Review your allowance before a sustained run. Model usage and the shared weekly quota are recorded separately.

## Try a method

Compare a single game against parallel lanes, mix both games, or change when the relay branches are prepared. Keep the model, reasoning effort, starting save and observation window constant across a comparison. Count uncached input plus output once per completed reply. Report changes in throughput alongside verified score improvements. The repository’s **New issue → 玩法挑战 / Throughput method** form accepts hypotheses as well as measured runs.

### Two separate throughput contests

| Contest | Timing and calculation | Report |
|---|---|---|
| **Speed** | One complete 600-second window. Count uncached input plus output; divide the window total by 10 for effective tokens per minute. | Ten one-minute buckets, unique completed reply count, speed, and a separate math result. |
| **Cumulative total** | Variable total duration. Sum only non-overlapping, complete 600-second windows. Deduplicate by unique reply ID so each reply counts once, and state the total elapsed duration. | Number of complete windows, per-window and grand totals, deduplicated replies, and a separate math result. Report any incomplete tail separately. |

To verify a cumulative series, pass its complete window directories in time order:

```sh
python -B benchmark_proof.py --series <first-window-dir> <second-window-dir>
```

Each directory represents one complete 600-second window. Keep the directories and elapsed-time summary with the result. Use the single-window proof command for a speed contest.

### The games and their math results

**Square packing** asks the model to find layouts for unit squares. The local geometry judge checks bounds and overlap; report the number of valid layouts and whether the enclosing square got smaller. **Four Realms Relay** uses lists to cover the 1,176 unordered pairs among 49 stations. Propose station swaps or relay branches; the local judge recomputes valid lists, covered pairs and the best score. Report math results separately from throughput: more tokens per minute do not prove a better solution, and a better solution does not prove the speed target was met.

Contributors with repository access may share methods, negative results and redacted receipt summaries. Do not attach private user archives or runs, personal/account/task IDs, quota readings, absolute paths, raw private prompts/replies or authorization details. Keep private originals and respect source-material permissions; share only materials you are allowed to share, such as a summary or a reproducible minimal example.

[For contributors with repository access: submit a method, negative result or measured summary](https://github.com/g6759/token-killer/issues/new?template=throughput-method.yml)

## Research games

These games have separate mathematical judges, packs and formal scenarios. Their game scores are not throughput scores. Existing speed or cumulative-token measurements came from the earlier Square packing and Four Realms drivers; they are not measured throughput results for these new games. Each pack has its own official single-lane scenario with the same ID as the pack.

| Pack | Objective | Single-lane scenario |
|---|---|---|
| `cap-set` | Find a larger cap set in four-dimensional ternary space. Coordinates are 0, 1 or 2; no three distinct points may sum to zero coordinatewise mod 3. | `/research?game=cap-set` |
| `golomb-ruler` | Place 8 marks so every pairwise difference is unique, then shorten the end-to-end ruler length. Reflections count as the same solution. | `/research?game=golomb-ruler` |
| `lorenz-divergence` | Adjust the initial value to study when two Lorenz trajectories first reach a separation threshold; the numerical judge cross-checks the result with independent methods. | `/research?game=lorenz-divergence` |

The same rules are available as `cap-set-5d`, `cap-set-6d`, `cap-set-7d`, `cap-set-8d`, `golomb-ruler-12` and `golomb-ruler-16`; each uses its matching `game` parameter at `/research?game=<id>`. The 8D page may load a historical 401-point construction as a reference. It is a checked historical construction, not a claim of priority or optimality. Player-generated constructions are labeled separately from the reference.
