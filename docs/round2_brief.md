# IGNITE Round 2 brief (team Doppler Effect)

The official brief is the PDF `docs/round2_brief.pdf`. **It is not in the repo yet: add it here.**
Until then, this file lists the acceptance criteria exactly as the team gave them to us.

## Goal
Add a **Policy Arbiter**. Every 10 s it picks the RL (PPO) action, the rules action, or nothing.
It decides with a short lookahead in the digital twin, picks the option with the lowest predicted
queue wait, and logs the source plus a one-line reason.

## Hard rules
- Extend this repo only. Keep changes small and additive.
- Do not change the Flask app structure, the SQLite schema, or existing routes.
- Do not retrain PPO. Do not modify `core/policy_weights.npz`
  (SHA256 `236323d9deb077274a652126a450fff5666bafe060c2b5020862037d6058f153`).
- No new model, no new scenarios, no UI redesign, no TinyFish changes, no deployment changes.

## Acceptance checks
- **mixed:** arbiter average wait ≤ rules average wait (10 seeds).
- **The other 5 scenarios:** arbiter within 5% of RL.
- The decision log has rl, rules and none entries, each with a reason.
- Force-fail (`--force-fail` or `ARBITER_FORCE_FAIL=1`): the run completes using the rules fallback.
- The app starts, the 3 demo logins work, and the Fleet and Simulation Lab pages return 200.
