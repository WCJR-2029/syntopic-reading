# Automated Phase 6 Readiness Monitoring

**Problem**: Don't want to manually remember when to implement Phase 6

**Solution**: Automated checker that tells you when you're ready

---

## Quick Start

### Manual Check (Run Anytime)

```bash
cd /path/to/repo
source .zen_venv/bin/activate
python scripts/monitoring/check_phase6_readiness.py
```

**Output**:
- Shows progress toward Phase 6 (0-100%)
- Lists what's missing (books, trails, ratings)
- Tells you next steps
- Exit code 0 if ready, 1 if not ready

---

## Automation Options

### Option 1: Weekly Cron Job (Recommended)

**What it does**: Checks every Monday morning, emails you if ready

**Setup**:
```bash
# Edit crontab
crontab -e

# Add this line (checks every Monday at 9 AM):
0 9 * * 1 cd /path/to/repo && source .zen_venv/bin/activate && python scripts/monitoring/check_phase6_readiness.py > /tmp/phase6_check.log 2>&1 && [ $? -eq 0 ] && echo "🎉 Phase 6 Ready! See /path/to/repo/PROJECT-ROADMAP.md" | mail -s "Syntopic System: Phase 6 Ready!" your-email@example.com
```

**How it works**:
- Runs every Monday at 9 AM
- Checks readiness silently
- Only emails you when Phase 6 is ready
- No spam until you hit 20 books + 50 trails

**Email setup** (if needed):
```bash
# Install mailutils
sudo apt install mailutils

# Test email
echo "Test" | mail -s "Test Subject" your-email@example.com
```

---

### Option 2: After Every Trail Generation

**What it does**: Checks readiness automatically after generating trails

**Setup** (Git post-commit hook):
```bash
# Create hook
cat > .git/hooks/post-commit << 'EOF'
#!/bin/bash
# Check Phase 6 readiness after commits that add trails

if git diff-tree --no-commit-id --name-only -r HEAD | grep -q "^data/trails/.*\.md$"; then
    echo ""
    echo "🔍 Checking Phase 6 readiness..."
    cd /path/to/repo
    source .zen_venv/bin/activate
    python scripts/monitoring/check_phase6_readiness.py
fi
EOF

chmod +x .git/hooks/post-commit
```

**How it works**:
- Runs after every git commit
- Only checks if you added trail files
- Shows progress in terminal
- No external dependencies

---

### Option 3: Weekly Desktop Notification

**What it does**: Shows desktop notification every Sunday evening

**Setup**:
```bash
# Create notification script
cat > ~/bin/check-syntopic-phase6.sh << 'EOF'
#!/bin/bash
cd /path/to/repo
source .zen_venv/bin/activate

# Run check and capture output
OUTPUT=$(python scripts/monitoring/check_phase6_readiness.py 2>&1)
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    # Phase 6 ready!
    notify-send "🎉 Syntopic System" "Phase 6 Ready! Time to implement self-improvement features." -u critical
else
    # Extract progress percentage
    PROGRESS=$(echo "$OUTPUT" | grep -oP '\d+%' | tail -1)
    notify-send "📚 Syntopic System" "Phase 6 progress: $PROGRESS" -u normal
fi
EOF

chmod +x ~/bin/check-syntopic-phase6.sh

# Add to crontab (every Sunday at 8 PM)
crontab -e
# Add: 0 20 * * 0 ~/bin/check-syntopic-phase6.sh
```

**How it works**:
- Shows desktop notification every Sunday evening
- Quick progress update (0-100%)
- Critical notification when ready
- Non-intrusive

---

### Option 4: Add to Morning Brief (If using Claude Code hooks)

**What it does**: Includes Phase 6 check in your daily Claude Code morning brief

**Setup**:
```bash
# Add a step to your daily-briefing automation, e.g.:
# - Run Phase 6 readiness check if the syntopic project is active
```

**Example output in morning brief**:
```
Open Loops:
- Syntopic System: Phase 6 progress 40% (need 8 more books, 30 more trails)
```

---

## Which Option to Choose?

**Recommended for most users**: Option 1 (Weekly Cron Job)
- Set it and forget it
- No spam (only emails when ready)
- Weekly check keeps you aware of progress

**If you generate trails frequently**: Option 2 (Git Hook)
- Automatic after every trail
- See progress immediately
- No email needed

**If you like desktop notifications**: Option 3 (Desktop Notification)
- Visual reminder
- Weekly check-in
- Non-intrusive

**If you use Claude Code morning briefs**: Option 4 (Morning Brief Integration)
- Part of daily routine
- Integrated with other project tracking
- Requires Claude Code hooks setup

**Multiple options work together**: You can use Options 1 + 2 together (cron + git hook)

---

## Manual Reminders (If You Prefer)

**Don't want automation?** Add to your task manager:

**Google Tasks**:
- Task: "Check syntopic system Phase 6 readiness"
- Repeat: Weekly
- Command: `python scripts/monitoring/check_phase6_readiness.py`

**Apple Reminders**:
- Reminder: "Syntopic Phase 6 check"
- Repeat: Every Monday
- Link: `/path/to/repo`

**Todoist / Notion / etc**:
- Recurring task with link to project

---

## What Happens When Ready?

**When you hit all thresholds** (20 books + 50 trails + ratings):

```bash
$ python scripts/monitoring/check_phase6_readiness.py

======================================================================
🎉 PHASE 6 READY!
======================================================================

You have enough data to implement Phase 6 self-improvement features.

Next steps:
  1. Review PROJECT-ROADMAP.md for Phase 6 components
  2. Analyze trail ratings to identify patterns
  3. Review coverage gaps and failed trails
  4. Decide which Phase 6 features to implement first
```

**You'll be notified via**:
- Terminal output (if running manually)
- Email (if using cron job)
- Desktop notification (if using Option 3)
- Morning brief (if using Option 4)

**Then you decide**: Start implementing Phase 6 or wait for more data

---

## Customizing Thresholds

**Want different thresholds?** Edit `scripts/monitoring/check_phase6_readiness.py`:

```python
# Change these values
MIN_BOOKS = 20          # Default: 20 books
MIN_TRAILS = 50         # Default: 50 trails
MIN_RATED_TRAILS = 50   # Default: 50 rated
```

**Examples**:
- Conservative: `MIN_TRAILS = 100` (wait for more data)
- Aggressive: `MIN_TRAILS = 30` (implement sooner)
- Minimal: `MIN_BOOKS = 10` (smaller library threshold)

---

## Troubleshooting

**Check not running?**
```bash
# Verify script works
python scripts/monitoring/check_phase6_readiness.py

# Check cron logs
grep CRON /var/log/syslog | tail -20
```

**Email not sending?**
```bash
# Test email
echo "Test" | mail -s "Test" your-email@example.com

# Install mail if missing
sudo apt install mailutils
```

**Wrong progress percentage?**
- Check if TRAIL-RATINGS.md exists and has "## Trail:" headers
- Verify books count: `sqlite3 data/database/syntopic.db "SELECT COUNT(DISTINCT title) FROM books;"`
- Count trail files: `ls -1 data/trails/*.md | wc -l`

---

**Bottom Line**: Set up Option 1 (cron job) and forget about it. The system will tell you when Phase 6 is ready!

