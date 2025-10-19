#!/usr/bin/env python3
"""
Script to analyze user sessions in the 'history' subsheet.

This script connects to your Google Sheets and analyzes the history data to:
1. Count unique days with translation records
2. Estimate the number of users by grouping translations into sessions
3. Show detailed session breakdown with start/end times and durations

Session Logic:
- Translations within 60 minutes are considered the same user session
- Gaps of 60+ minutes indicate a new user session
- Each session shows: date, start time, end time, translation count, duration

Usage:
    python count_unique_days.py

Requirements:
    - Google Sheets credentials file (Form Translator DB IAM.json)
    - Internet connection to access Google Sheets API
"""

import os
import sys
from datetime import datetime, timedelta
from collections import defaultdict

# Add the current directory to the path so we can import our modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sheets_service import SheetsService

def parse_datetime_string(date_str):
    """
    Parse datetime string using the same logic as the main application.
    Returns a datetime object or None if parsing fails.
    """
    if not date_str or not date_str.strip():
        return None
    
    # Try different datetime formats that might be in the sheet
    formats_to_try = [
        '%m/%d/%Y %H:%M:%S',  # Primary format used by the app
        '%m/%d/%Y',           # Date only
        '%Y-%m-%d %H:%M:%S',  # ISO format with time
        '%Y-%m-%d',           # ISO format date only
    ]
    
    for fmt in formats_to_try:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    
    # Try ISO format parsing as fallback
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except:
        pass
    
    return None

def extract_date_from_datetime(dt_obj):
    """
    Extract just the date part (YYYY-MM-DD) from a datetime object.
    """
    if dt_obj is None:
        return None
    return dt_obj.date()

def group_translations_by_sessions(translations_with_datetime, session_gap_minutes=60):
    """
    Group translations into user sessions based on time gaps.
    
    Args:
        translations_with_datetime: List of tuples (datetime_obj, record)
        session_gap_minutes: Minutes gap to consider as a new session (default: 60)
    
    Returns:
        List of sessions, where each session is a dict with:
        - date: date object
        - start_time: datetime of first translation
        - end_time: datetime of last translation  
        - translations: list of records in this session
        - count: number of translations
    """
    if not translations_with_datetime:
        return []
    
    # Sort by datetime
    sorted_translations = sorted(translations_with_datetime, key=lambda x: x[0])
    
    sessions = []
    current_session = []
    
    for i, (dt_obj, record) in enumerate(sorted_translations):
        if not current_session:
            # Start first session
            current_session = [(dt_obj, record)]
        else:
            # Check time gap with previous translation
            prev_dt = current_session[-1][0]
            time_gap = dt_obj - prev_dt
            
            if time_gap <= timedelta(minutes=session_gap_minutes):
                # Same session - add to current session
                current_session.append((dt_obj, record))
            else:
                # New session - save current session and start new one
                if current_session:
                    session = create_session_from_translations(current_session)
                    sessions.append(session)
                current_session = [(dt_obj, record)]
    
    # Don't forget the last session
    if current_session:
        session = create_session_from_translations(current_session)
        sessions.append(session)
    
    return sessions

def create_session_from_translations(session_translations):
    """
    Create a session object from a list of translations.
    """
    if not session_translations:
        return None
    
    # Sort by datetime to get start and end times
    sorted_trans = sorted(session_translations, key=lambda x: x[0])
    start_time = sorted_trans[0][0]
    end_time = sorted_trans[-1][0]
    date = start_time.date()
    
    # Extract just the records
    records = [record for _, record in session_translations]
    
    return {
        'date': date,
        'start_time': start_time,
        'end_time': end_time,
        'translations': records,
        'count': len(records)
    }

def count_unique_days_in_history():
    """
    Main function to count unique days in the history subsheet.
    """
    print("🔍 Analyzing history subsheet to count unique days...")
    print("=" * 60)
    
    try:
        # Initialize the sheets service
        print("📊 Connecting to Google Sheets...")
        sheets_service = SheetsService()
        
        # Set the sheet ID (same as used in your main app)
        sheet_id = "1sEnmusmz4X_18emilcsLFIn48nwM6qInLgQKN2rXC5M"
        sheets_service.set_sheet_id(sheet_id)
        
        print(f"✅ Connected to sheet: {sheet_id}")
        
        # Get all history data
        print("📋 Retrieving history data...")
        history_data = sheets_service.get_history_data("history")
        
        if not history_data:
            print("❌ No history data found in the 'history' subsheet")
            return
        
        print(f"📊 Found {len(history_data)} total history records")
        
        # Analyze datetime column and group by sessions
        print("\n🕒 Analyzing datetime column and grouping by user sessions...")
        
        unique_dates = set()
        parse_errors = []
        date_counts = defaultdict(int)  # Count how many records per date
        translations_with_datetime = []  # Store (datetime_obj, record) tuples
        
        for i, record in enumerate(history_data):
            datetime_str = record.get('datetime', '')
            
            # Parse the datetime string
            dt_obj = parse_datetime_string(datetime_str)
            
            if dt_obj is None:
                parse_errors.append({
                    'row': i + 2,  # +2 because of 1-based indexing and header row
                    'datetime_str': datetime_str,
                    'record_id': record.get('id', 'unknown')
                })
                continue
            
            # Extract just the date part
            date_only = extract_date_from_datetime(dt_obj)
            
            if date_only:
                unique_dates.add(date_only)
                date_counts[date_only] += 1
                translations_with_datetime.append((dt_obj, record))
        
        # Group translations by user sessions (60-minute gap = new user)
        print("👥 Grouping translations into user sessions...")
        sessions = group_translations_by_sessions(translations_with_datetime, session_gap_minutes=60)
        
        # Group sessions by date
        sessions_by_date = defaultdict(list)
        for session in sessions:
            sessions_by_date[session['date']].append(session)
        
        # Display results
        print("\n" + "=" * 60)
        print("📈 RESULTS")
        print("=" * 60)
        
        print(f"🎯 Total unique days with translations: {len(unique_dates)}")
        print(f"📊 Total history records analyzed: {len(history_data)}")
        print(f"👥 Estimated user sessions: {len(sessions)}")
        
        if parse_errors:
            print(f"⚠️  Records with datetime parsing errors: {len(parse_errors)}")
        
        # Show detailed session breakdown
        if sessions_by_date:
            print(f"\n📅 Detailed session breakdown (60+ minute gap = new user):")
            print(f"{'Date':<12} {'Start Time':<12} {'End Time':<12} {'Translations':<12} {'Session Duration'}")
            print("-" * 70)
            
            sorted_dates = sorted(sessions_by_date.keys(), reverse=True)  # Most recent first
            
            for date in sorted_dates:
                date_sessions = sessions_by_date[date]
                # Sort sessions within each date by start time
                date_sessions.sort(key=lambda s: s['start_time'])
                
                for session in date_sessions:
                    start_time = session['start_time'].strftime('%H:%M:%S')
                    end_time = session['end_time'].strftime('%H:%M:%S')
                    count = session['count']
                    
                    # Calculate session duration
                    duration = session['end_time'] - session['start_time']
                    duration_str = str(duration).split('.')[0]  # Remove microseconds
                    if duration_str.startswith('0:'):
                        duration_str = duration_str[2:]  # Remove '0:' prefix for durations < 1 hour
                    
                    print(f"{date.strftime('%Y-%m-%d')} {start_time:<12} {end_time:<12} {count:<12} {duration_str}")
        
        # Show summary statistics
        if sessions:
            print(f"\n📊 Session Statistics:")
            session_counts = [s['count'] for s in sessions]
            total_users = len(sessions)
            avg_translations_per_session = sum(session_counts) / len(session_counts)
            max_session = max(sessions, key=lambda s: s['count'])
            min_session = min(sessions, key=lambda s: s['count'])
            
            print(f"   Total estimated users: {total_users}")
            print(f"   Average translations per session: {avg_translations_per_session:.1f}")
            print(f"   Largest session: {max_session['count']} translations on {max_session['date'].strftime('%Y-%m-%d')}")
            print(f"   Smallest session: {min_session['count']} translation(s) on {min_session['date'].strftime('%Y-%m-%d')}")
        
        # Show parsing errors if any
        if parse_errors:
            print(f"\n⚠️  Datetime parsing errors:")
            for error in parse_errors[:5]:  # Show first 5 errors
                print(f"   Row {error['row']}: '{error['datetime_str']}' (ID: {error['record_id']})")
            
            if len(parse_errors) > 5:
                print(f"   ... and {len(parse_errors) - 5} more parsing errors")
        
        print("\n" + "=" * 60)
        print("✅ Analysis complete!")
        
    except FileNotFoundError as e:
        print(f"❌ Credentials file not found: {e}")
        print("💡 Make sure 'Form Translator DB IAM.json' is in the same directory as this script")
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        print("💡 Make sure you have internet connection and proper Google Sheets permissions")

def main():
    """
    Main entry point for the script.
    """
    print("🚀 Form Translator - History Days Counter")
    print("This script counts unique days in your translation history")
    print()
    
    count_unique_days_in_history()

if __name__ == "__main__":
    main()
