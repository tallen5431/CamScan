# Deployment Fix: Merge Conflict Markers

## Issue
Your deployment server has Git merge conflict markers in `calibration_core.py` causing a SyntaxError:
```
File "/home/jupyter-tj/projects/CamScan/calibration_core.py", line 1
    <<<<<<< Updated upstream
    ^^
SyntaxError: invalid syntax
```

## Root Cause
During a git merge/pull operation on your deployment server, Git encountered conflicts and inserted conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) into the file. These markers were not resolved before the file was committed or used.

## Solution Options

### Option 1: Pull Clean Version from Git (Recommended)
On your deployment server, run:

```bash
cd /home/jupyter-tj/projects/CamScan

# Discard local changes and pull clean version
git fetch origin claude/review-camscan-detection-01X2nMtEyZTEQhfWen5TmLgg
git reset --hard origin/claude/review-camscan-detection-01X2nMtEyZTEQhfWen5TmLgg

# Or if you're on WorkingDetection:
git fetch origin WorkingDetection
git reset --hard origin/WorkingDetection

# Restart your app
```

### Option 2: Manual Conflict Resolution
If you have local changes you want to keep:

```bash
cd /home/jupyter-tj/projects/CamScan

# Check which files have conflicts
git status

# For each conflicted file, edit it and:
# 1. Find lines starting with <<<<<<< (conflict start)
# 2. Find lines with ======= (separator)
# 3. Find lines with >>>>>>> (conflict end)
# 4. Choose which version to keep
# 5. Delete ALL conflict markers

# Example conflict markers to remove:
<<<<<<< Updated upstream
[some code]
=======
[other code]
>>>>>>> Stashed changes

# After editing, mark as resolved:
git add calibration_core.py detect_squares.py edge_finder.py

# Commit the resolution:
git commit -m "Resolve merge conflicts"
```

### Option 3: Quick Fix Script
Create and run this script on your server:

```bash
cat > /tmp/fix_conflicts.sh << 'EOF'
#!/bin/bash
cd /home/jupyter-tj/projects/CamScan

# Backup current files
cp calibration_core.py calibration_core.py.backup.$(date +%s)
cp detect_squares.py detect_squares.py.backup.$(date +%s)
cp edge_finder.py edge_finder.py.backup.$(date +%s)

# Download clean versions from GitHub
BRANCH="claude/review-camscan-detection-01X2nMtEyZTEQhfWen5TmLgg"
curl -H "Authorization: token YOUR_GITHUB_TOKEN" \
  https://raw.githubusercontent.com/tallen5431/CamScan/${BRANCH}/calibration_core.py \
  -o calibration_core.py

curl -H "Authorization: token YOUR_GITHUB_TOKEN" \
  https://raw.githubusercontent.com/tallen5431/CamScan/${BRANCH}/detect_squares.py \
  -o detect_squares.py

curl -H "Authorization: token YOUR_GITHUB_TOKEN" \
  https://raw.githubusercontent.com/tallen5431/CamScan/${BRANCH}/edge_finder.py \
  -o edge_finder.py

echo "Files updated. Backups saved with .backup suffix."
EOF

chmod +x /tmp/fix_conflicts.sh
/tmp/fix_conflicts.sh
```

## Verification
After fixing, verify the files are clean:

```bash
cd /home/jupyter-tj/projects/CamScan

# Check for any remaining conflict markers
grep -r "<<<<<<" . --include="*.py"
grep -r "=======" . --include="*.py"
grep -r ">>>>>>>" . --include="*.py"

# If no output, you're clean!

# Test Python syntax
python3 -m py_compile calibration_core.py detect_squares.py edge_finder.py

# If no errors, restart your app
```

## Prevention
To avoid this in the future:

1. **Always check git status before pulling:**
   ```bash
   git status
   # If you have uncommitted changes, either commit or stash them first
   ```

2. **Use git stash when pulling:**
   ```bash
   git stash push -m "WIP: saving local changes"
   git pull
   git stash pop  # Then resolve any conflicts properly
   ```

3. **Set up git merge tool:**
   ```bash
   git config --global merge.tool vimdiff  # or meld, kdiff3, etc.
   git config --global merge.conflictstyle diff3
   ```

4. **Check for conflict markers in CI/CD:**
   Add to your deployment script:
   ```bash
   if grep -r "<<<<<<" . --include="*.py" --quiet; then
     echo "ERROR: Merge conflict markers found!"
     exit 1
   fi
   ```

## Current State of Git Repository
The git repository itself is CLEAN - the issue is only on your deployment server. The following branches have clean, working code:

- ✅ `claude/review-camscan-detection-01X2nMtEyZTEQhfWen5TmLgg` (clean, latest improvements)
- ✅ `WorkingDetection` (clean, original working version)

## Need Help?
If the above solutions don't work, you can:

1. Share the output of: `git status` from your deployment server
2. Share the first 50 lines of the problematic file: `head -50 calibration_core.py`
3. Check if you have multiple Python environments: `which python3` and `python3 --version`
