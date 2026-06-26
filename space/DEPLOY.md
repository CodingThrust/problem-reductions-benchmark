# Deploying the static leaderboard Space

This directory holds the **HF Space scaffolding**. The deployable bundle is
*generated* into `space/site/` by `benchmark/build_space.py` (it is not committed —
see `.gitignore`). `space/site/` mirrors the GitHub Pages layout, so it renders
identically when served from a directory root.

## 1. Build the bundle

```bash
make space            # → space/site/{README.md,index.html,leaderboard/,results/}
```

## 2. Preview locally (do this before touching HF)

```bash
make space-serve      # serves space/site/ at http://localhost:8000
# open http://localhost:8000  → should redirect to the leaderboard and render the table
```

## 3. Create the Space — PRIVATE first

Keep it private until the rendering is validated. Do **not** make it public yet.

```bash
# one-time: create a private static Space (replace <user>)
hf repo create <user>/problem-reductions-benchmark --repo-type space --space-sdk static --private

# push the generated bundle
git clone https://huggingface.co/spaces/<user>/problem-reductions-benchmark hf-space
cp -R space/site/. hf-space/
cd hf-space && git add -A && git commit -m "leaderboard snapshot" && git push
```

Visit the private Space URL, confirm it renders, then flip visibility to public
in the Space settings when ready.

## 4. (Later) Auto-sync from GitHub

Once validated, a GitHub Action can rebuild the index and push `space/site/` to the
Space on every change to `results/**`. That needs an `HF_TOKEN` repo secret. Not set
up yet — intentionally manual while testing.
