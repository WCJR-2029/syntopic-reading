#!/usr/bin/env python3
"""
Phase 6 Readiness Checker

Monitors system state and alerts when ready to implement Phase 6 self-improvement features.

Run manually: python scripts/monitoring/check_phase6_readiness.py
Run on cron: Add to crontab for weekly checks
Run on trail generation: Add as git hook
"""

import sys
import os
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent.parent

from src.database.operations import get_db


class Phase6ReadinessChecker:
    """Checks if system has enough data to implement Phase 6"""
    
    # Thresholds from PROJECT-ROADMAP.md
    MIN_BOOKS = 20
    MIN_TRAILS = 50
    MIN_RATED_TRAILS = 50
    
    def __init__(self):
        self.checks_passed = []
        self.checks_failed = []
        self.warnings = []
        
    def check_book_count(self):
        """Check if library has enough books"""
        with get_db() as db:
            cursor = db.conn.cursor()
            cursor.execute("SELECT COUNT(DISTINCT title) FROM books")
            book_count = cursor.fetchone()[0]
        
        if book_count >= self.MIN_BOOKS:
            self.checks_passed.append(f"✅ Books: {book_count}/{self.MIN_BOOKS} (READY)")
            return True
        else:
            remaining = self.MIN_BOOKS - book_count
            self.checks_failed.append(f"❌ Books: {book_count}/{self.MIN_BOOKS} (need {remaining} more)")
            return False
    
    def check_trail_count(self):
        """Check if enough trails have been generated"""
        trails_dir = PROJECT_ROOT / "data" / "trails"
        
        if not trails_dir.exists():
            self.checks_failed.append(f"❌ Trails: 0/{self.MIN_TRAILS} (no trails directory)")
            return False
        
        # Count markdown files (exclude TRAIL-RATINGS.md and other meta files)
        trail_files = [f for f in trails_dir.glob("*.md") 
                      if f.name not in ["TRAIL-RATINGS.md", "README.md"]]
        trail_count = len(trail_files)
        
        if trail_count >= self.MIN_TRAILS:
            self.checks_passed.append(f"✅ Trails generated: {trail_count}/{self.MIN_TRAILS} (READY)")
            return True
        else:
            remaining = self.MIN_TRAILS - trail_count
            self.checks_failed.append(f"❌ Trails generated: {trail_count}/{self.MIN_TRAILS} (need {remaining} more)")
            return False
    
    def check_trail_ratings(self):
        """Check if trails have been rated"""
        ratings_file = PROJECT_ROOT / "data" / "trails" / "TRAIL-RATINGS.md"
        
        if not ratings_file.exists():
            self.checks_failed.append(f"❌ Trail ratings: File not found (create TRAIL-RATINGS.md)")
            self.warnings.append("   Create data/trails/TRAIL-RATINGS.md to start tracking quality")
            return False
        
        # Count rated trails (look for "## Trail:" headers)
        content = ratings_file.read_text()
        rated_count = content.count("## Trail:")
        
        if rated_count >= self.MIN_RATED_TRAILS:
            self.checks_passed.append(f"✅ Rated trails: {rated_count}/{self.MIN_RATED_TRAILS} (READY)")
            return True
        elif rated_count > 0:
            remaining = self.MIN_RATED_TRAILS - rated_count
            self.checks_failed.append(f"❌ Rated trails: {rated_count}/{self.MIN_RATED_TRAILS} (need {remaining} more)")
            return False
        else:
            self.checks_failed.append(f"❌ Rated trails: 0/{self.MIN_RATED_TRAILS} (start rating trails)")
            self.warnings.append("   See USAGE-GUIDE.md for trail rating template")
            return False
    
    def check_coverage_gaps_documented(self):
        """Check if user has identified coverage gaps"""
        # This is optional but helpful
        gap_indicators = [
            PROJECT_ROOT / "COVERAGE-GAPS.md",
            PROJECT_ROOT / "TODO-BOOKS.md",
            PROJECT_ROOT / "data" / "trails" / "FAILED-TRAILS.md"
        ]
        
        found_gaps = any(f.exists() for f in gap_indicators)
        
        if found_gaps:
            self.checks_passed.append("✅ Coverage gaps documented (good practice)")
            return True
        else:
            self.warnings.append("⚠️  No coverage gap documentation found (optional but helpful)")
            self.warnings.append("   Consider creating COVERAGE-GAPS.md or FAILED-TRAILS.md")
            return False
    
    def calculate_progress(self):
        """Calculate overall progress toward Phase 6"""
        total_checks = 3  # Books, trails, ratings (required)
        passed_checks = len([c for c in self.checks_passed if "READY" in c])
        
        progress_pct = (passed_checks / total_checks) * 100
        return progress_pct, passed_checks, total_checks
    
    def generate_report(self):
        """Generate readiness report"""
        print("\n" + "="*70)
        print("PHASE 6 READINESS CHECK")
        print("="*70)
        print(f"Checked: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        # Run all checks
        book_ready = self.check_book_count()
        trail_ready = self.check_trail_count()
        rating_ready = self.check_trail_ratings()
        self.check_coverage_gaps_documented()  # Optional
        
        # Show results
        print("Required Criteria:")
        for check in self.checks_passed:
            if "READY" in check:
                print(f"  {check}")
        
        for check in self.checks_failed:
            print(f"  {check}")
        
        if self.warnings:
            print("\nOptional (Helpful but not required):")
            for warning in self.warnings:
                print(f"  {warning}")
        
        # Progress bar
        progress_pct, passed, total = self.calculate_progress()
        print(f"\nProgress: {passed}/{total} required checks passed ({progress_pct:.0f}%)")
        
        bar_length = 50
        filled = int(bar_length * progress_pct / 100)
        bar = "█" * filled + "░" * (bar_length - filled)
        print(f"[{bar}] {progress_pct:.0f}%")
        
        # Final verdict
        print("\n" + "="*70)
        if book_ready and trail_ready and rating_ready:
            print("🎉 PHASE 6 READY!")
            print("="*70)
            print("\nYou have enough data to implement Phase 6 self-improvement features.")
            print("\nNext steps:")
            print("  1. Review PROJECT-ROADMAP.md for Phase 6 components")
            print("  2. Analyze trail ratings to identify patterns")
            print("  3. Review coverage gaps and failed trails")
            print("  4. Decide which Phase 6 features to implement first")
            print("\nRecommended implementation order:")
            print("  - 6.1: Trail Quality Feedback System (2-3 days)")
            print("  - 6.2: Coverage Gap Analysis (3-4 days)")
            print("  - 6.3: Trail Generation Improvement Engine (4-5 days)")
            print("\nSee PROJECT-ROADMAP.md for detailed specifications.")
            return 0  # Success exit code
        else:
            print("📋 NOT YET READY - Keep using the system!")
            print("="*70)
            print("\nWhat to do next:")
            
            if not book_ready:
                remaining = self.MIN_BOOKS - self._get_book_count()
                print(f"  - Process {remaining} more books (see TODO-BOOK-ACQUISITION.md)")
            
            if not trail_ready:
                remaining = self.MIN_TRAILS - self._get_trail_count()
                print(f"  - Generate {remaining} more trails")
                print(f"    Command: python -m src.agent.trail_generator --count {min(10, remaining)}")
            
            if not rating_ready:
                print(f"  - Rate your trails in data/trails/TRAIL-RATINGS.md")
                print(f"    Template in USAGE-GUIDE.md")
            
            print("\nRun this check again after making progress:")
            print("  python scripts/monitoring/check_phase6_readiness.py")
            
            return 1  # Not ready exit code
    
    def _get_book_count(self):
        """Helper to get current book count"""
        with get_db() as db:
            cursor = db.conn.cursor()
            cursor.execute("SELECT COUNT(DISTINCT title) FROM books")
            return cursor.fetchone()[0]
    
    def _get_trail_count(self):
        """Helper to get current trail count"""
        trails_dir = PROJECT_ROOT / "data" / "trails"
        if not trails_dir.exists():
            return 0
        trail_files = [f for f in trails_dir.glob("*.md") 
                      if f.name not in ["TRAIL-RATINGS.md", "README.md"]]
        return len(trail_files)


def main():
    """Main entry point"""
    checker = Phase6ReadinessChecker()
    exit_code = checker.generate_report()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
