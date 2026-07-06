#!/bin/bash
# Setup weekly Phase 6 readiness check (cron job)

echo "Setting up weekly Phase 6 readiness check..."
echo ""

# Get user email
read -p "Enter your email for notifications (or press Enter to skip): " USER_EMAIL

if [ -z "$USER_EMAIL" ]; then
    echo "Skipping email notifications. You can add them later."
    echo ""
    echo "To check manually, run:"
    echo "  python scripts/monitoring/check_phase6_readiness.py"
    exit 0
fi

# Check if mail is installed
if ! command -v mail &> /dev/null; then
    echo ""
    echo "Mail command not found. Installing mailutils..."
    sudo apt install -y mailutils
fi

# Test email
echo "Testing email to $USER_EMAIL..."
echo "This is a test from Syntopic Reading System" | mail -s "Test Email - Syntopic System" "$USER_EMAIL"

if [ $? -eq 0 ]; then
    echo "✅ Test email sent! Check your inbox."
else
    echo "❌ Email test failed. You may need to configure mail settings."
    exit 1
fi

# Add to crontab
PROJECT_DIR=$(pwd)
CRON_LINE="0 9 * * 1 cd $PROJECT_DIR && source .zen_venv/bin/activate && python scripts/monitoring/check_phase6_readiness.py > /tmp/phase6_check.log 2>&1 && [ \$? -eq 0 ] && echo '🎉 Phase 6 Ready! See $PROJECT_DIR/PROJECT-ROADMAP.md' | mail -s 'Syntopic System: Phase 6 Ready!' $USER_EMAIL"

# Check if cron line already exists
if crontab -l 2>/dev/null | grep -q "check_phase6_readiness.py"; then
    echo ""
    echo "⚠️  Cron job already exists. Not adding duplicate."
else
    # Add to crontab
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
    
    if [ $? -eq 0 ]; then
        echo ""
        echo "✅ Weekly check scheduled!"
        echo ""
        echo "Details:"
        echo "  - Runs every Monday at 9 AM"
        echo "  - Checks Phase 6 readiness"
        echo "  - Emails you at $USER_EMAIL when ready"
        echo ""
        echo "To check status manually:"
        echo "  python scripts/monitoring/check_phase6_readiness.py"
        echo ""
        echo "To remove:"
        echo "  crontab -e  # then delete the syntopic line"
    else
        echo "❌ Failed to add cron job."
        exit 1
    fi
fi
