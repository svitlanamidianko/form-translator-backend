"""
Google Sheets Service for Form Translator Backend

This module handles all Google Sheets operations for storing forms data.
The "forms" tab structure (what is ACTUALLY in the cells - the header row says
something slightly different, and we never rewrite the header row):
- Column A: id (number)
- Column B: form name
- Column C: Ukrainian translation of the form name   (header says "form description")
- Column D: English description of the form's style  (header says "category")
- Column E: semantic group, e.g. "religion"          (header says "user_submitted")
            type "hidden" here to hide a form from the website's list.
            Older visitor-added rows have TRUE here instead of a group.
- Column F: user_submitted (TRUE for forms added by visitors)
"""

import os
import json
import re
import threading
import time
from typing import List, Dict, Optional
import google_auth_httplib2
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest, build_http

# Cell values that mean "yes" / "no" in the user_submitted columns
TRUE_LIKE_VALUES = {"true", "1", "yes"}
FALSE_LIKE_VALUES = {"false", "0", "no"}

# Used when a brand new (empty) forms tab gets its first header row
DEFAULT_FORMS_HEADERS = ['id', 'form name', 'ukrainian name', 'description', 'group', 'user_submitted']

# Only one thread at a time may "pick the next id + append a row" (otherwise two rows could get the same id)
_form_write_lock = threading.Lock()


def normalize_form_name(name) -> str:
    """
    Normalize a form name for duplicate checks:
    "  Gen   Z " -> "gen z", "socialism " -> "socialism"
    (trim + collapse inner spaces + casefold, which is like lower() but smarter for non-English letters)
    """
    if name is None:
        return ""
    return re.sub(r"\s+", " ", str(name)).strip().casefold()


def clean_form_name(name) -> str:
    """Trim a form name and collapse inner whitespace, keeping the original letter case"""
    if name is None:
        return ""
    return re.sub(r"\s+", " ", str(name)).strip()


def is_true_like(value) -> bool:
    """True for TRUE / true / 1 / yes (and real booleans)"""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in TRUE_LIKE_VALUES


def is_bool_like(value) -> bool:
    """True when a cell looks like a yes/no flag rather than real text (TRUE, FALSE, 1, 0, yes, no)"""
    if isinstance(value, bool):
        return True
    if value is None:
        return False
    return str(value).strip().lower() in (TRUE_LIKE_VALUES | FALSE_LIKE_VALUES)


def parse_form_row(row: list, sheet_row: int) -> Dict:
    """
    Convert one raw row from the forms tab (A:F) into a dictionary.

    Args:
        row: list of cell values as returned by the Sheets API (may be shorter than 6)
        sheet_row: the row number in the sheet (1-based, the header is row 1)
    """
    def cell(index):
        """Raw cell value ("" when the row is shorter than that column)"""
        value = row[index] if len(row) > index else ""
        return "" if value is None else value

    def text(index):
        return str(cell(index)).strip()

    column_e = cell(4)
    column_f = cell(5)

    # Column E is normally the group, but older visitor rows have TRUE there
    if is_bool_like(column_e):
        group = ""
    else:
        group = clean_form_name(column_e)

    return {
        'form_id': text(0),
        'form_name': str(cell(1)),           # raw name (may have stray spaces - use clean_form_name)
        'ukrainian_name': text(2),           # column C
        'description': text(3),              # column D - English description of the style
        'group': group,                      # column E (or "" when E is TRUE/FALSE/empty)
        'user_submitted': is_true_like(column_e) or is_true_like(column_f),
        'row_number': sheet_row,
    }


def build_sheets_api(credentials, **build_options):
    """
    Build the Google Sheets API client so several threads can use it at the same time.

    The normal build('sheets', 'v4', credentials=...) shares ONE connection object (httplib2.Http)
    between all threads, and that object is not thread-safe: when a request and a background job
    (e.g. saving a visitor's new form) talk to Google at the same moment, they can get each other's
    answers or time out. Here every thread gets its own connection - like giving each worker their
    own phone line instead of one shared phone. (Recipe from the google-api-python-client
    "thread safety" docs.)

    build_options: extra options for build() (the tests use it to point at a local fake server)
    """
    per_thread = threading.local()

    def http_for_this_thread():
        if getattr(per_thread, "http", None) is None:
            per_thread.http = google_auth_httplib2.AuthorizedHttp(credentials, http=build_http())
        return per_thread.http

    def build_request(_shared_http, *args, **kwargs):
        return HttpRequest(http_for_this_thread(), *args, **kwargs)

    return build('sheets', 'v4', http=http_for_this_thread(), requestBuilder=build_request, **build_options)


class SheetsService:
    def __init__(self, credentials_file: str = None, sheet_id: str = None):
        """
        Initialize the Google Sheets service
        
        Args:
            credentials_file: Path to the service account JSON file (defaults to env var or fallback)
            sheet_id: Google Sheets ID (the long string in the sheet URL)
        """
        # Use provided file, environment variable, or fallback
        self.credentials_file = (
            credentials_file or 
            os.environ.get('GOOGLE_APPLICATION_CREDENTIALS') or 
            "Form Translator DB IAM.json"
        )
        self.sheet_id = sheet_id
        self.service = None
        self._initialize_service()
    
    def _initialize_service(self):
        """Initialize the Google Sheets API service"""
        try:
            # Define the scope for Google Sheets API
            scopes = ['https://www.googleapis.com/auth/spreadsheets']
            
            # Try to load credentials from environment variable first (for production)
            credentials_json = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS_JSON')
            if credentials_json:
                # Load credentials from JSON string (production)
                credentials_info = json.loads(credentials_json)
                credentials = Credentials.from_service_account_info(
                    credentials_info, 
                    scopes=scopes
                )
                print("✅ Using Google credentials from environment variable")
            else:
                # Load credentials from JSON file (development)
                if not os.path.exists(self.credentials_file):
                    raise FileNotFoundError(f"Credentials file not found: {self.credentials_file}")
                
                credentials = Credentials.from_service_account_file(
                    self.credentials_file, 
                    scopes=scopes
                )
                print(f"✅ Using Google credentials from file: {self.credentials_file}")
            
            # Build the service (safe to use from several threads at once - see build_sheets_api)
            self.service = build_sheets_api(credentials)
            print("✅ Google Sheets service initialized successfully!")
            
        except Exception as e:
            print(f"❌ Failed to initialize Google Sheets service: {str(e)}")
            raise
    
    def set_sheet_id(self, sheet_id: str):
        """Set the Google Sheets ID"""
        self.sheet_id = sheet_id
        print(f"📊 Sheet ID set to: {sheet_id}")
    
    def create_headers_if_needed(self, sheet_name: str = "forms"):
        """
        Create headers in the forms tab ONLY if the header row is completely empty.
        An existing header row is never rewritten (even if its text differs from ours),
        because the real column meaning is documented at the top of this file.
        Headers for a new tab: id | form name | ukrainian name | description | group | user_submitted
        """
        try:
            if not self.sheet_id:
                raise ValueError("Sheet ID not set. Use set_sheet_id() method.")
            
            # Check if headers exist
            range_name = f"{sheet_name}!A1:F1"
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=range_name
            ).execute()
            
            values = result.get('values', [])
            header_row = values[0] if values else []
            header_is_empty = not any(str(cell).strip() for cell in header_row)
            
            # Only write headers into a completely empty first row
            if header_is_empty:
                body = {
                    'values': [DEFAULT_FORMS_HEADERS]
                }
                
                self.service.spreadsheets().values().update(
                    spreadsheetId=self.sheet_id,
                    range=f"{sheet_name}!A1:F1",
                    valueInputOption='RAW',
                    body=body
                ).execute()
                
                print("✅ Headers created in Google Sheet")
            else:
                print("✅ Headers already exist (left untouched)")
                
        except HttpError as e:
            print(f"❌ HTTP Error creating headers: {e}")
            raise
        except Exception as e:
            print(f"❌ Error creating headers: {str(e)}")
            raise
    
    def add_form(self, form_name: str, ukrainian_name: str = "", description: str = "", group: str = "",
                 user_submitted: bool = False, sheet_name: str = "forms") -> bool:
        """
        Add a new form to the Google Sheet (writes columns A:F)
        
        Args:
            form_name: Name of the form (column B)
            ukrainian_name: Ukrainian translation of the name (column C, optional)
            description: English description of the form's style (column D, optional)
            group: Semantic group (column E, optional - "hidden" hides it from the website)
            user_submitted: Whether this is a user-submitted custom form (column F, default: False)
            sheet_name: Name of the sheet tab (default: "forms")
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if not self.sheet_id:
                raise ValueError("Sheet ID not set. Use set_sheet_id() method.")
            
            # Ensure headers exist
            self.create_headers_if_needed(sheet_name)
            
            # One writer at a time, so two new forms never get the same id
            with _form_write_lock:
                # Get next form ID
                next_id = self._get_next_form_id(sheet_name)
            
                # Prepare the data to append: id | name | ukrainian | description | group | user_submitted
                values = [[next_id, form_name, ukrainian_name, description, group, user_submitted]]
            
                body = {
                    'values': values
                }
            
                # Append the data to the sheet
                result = self.service.spreadsheets().values().append(
                    spreadsheetId=self.sheet_id,
                    range=f"{sheet_name}!A:F",
                    valueInputOption='RAW',
                    insertDataOption='INSERT_ROWS',
                    body=body
                ).execute()
            
            print(f"✅ Form '{form_name}' added to Google Sheet (ID: {next_id}, group: '{group}', user_submitted: {user_submitted})")
            return True
            
        except HttpError as e:
            print(f"❌ HTTP Error adding form: {e}")
            return False
        except Exception as e:
            print(f"❌ Error adding form: {str(e)}")
            return False
    
    def _get_next_form_id(self, sheet_name: str = "forms") -> int:
        """
        Get the next available form ID
        
        Args:
            sheet_name: Name of the sheet tab
            
        Returns:
            int: Next available form ID
        """
        try:
            # Get all data from column A (Form ID)
            range_name = f"{sheet_name}!A:A"
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=range_name
            ).execute()
            
            values = result.get('values', [])
            
            if not values or len(values) <= 1:  # No data or only headers
                return 1
            
            # Find the highest ID
            max_id = 0
            for row in values[1:]:  # Skip header row
                if len(row) > 0 and row[0]:
                    try:
                        form_id = int(row[0])
                        max_id = max(max_id, form_id)
                    except (ValueError, TypeError):
                        continue
            
            return max_id + 1
            
        except Exception as e:
            # Don't fall back to 1 (that is how we got two "PHILOSOPHY" rows with ids 1 and 2).
            # A timestamp-based number is unique enough and easy to spot in the sheet.
            fallback_id = int(time.time())
            print(f"❌ Error getting next form ID: {str(e)} - using timestamp-based id {fallback_id} instead")
            return fallback_id
    
    def get_all_forms(self, sheet_name: str = "forms", include_user_submitted: bool = True,
                      raise_errors: bool = False) -> List[Dict]:
        """
        Get all forms from the Google Sheet (columns A:F, see the top of this file)
        
        Args:
            sheet_name: Name of the sheet tab (default: "forms")
            include_user_submitted: Whether to include user-submitted forms (default: True)
            raise_errors: re-raise errors instead of returning [] (so callers can tell
                          "the sheet is empty" apart from "we couldn't read the sheet")
            
        Returns:
            List of dictionaries (in sheet order) with keys:
            form_id, form_name, ukrainian_name, description, group, user_submitted, row_number
        """
        try:
            if not self.sheet_id:
                raise ValueError("Sheet ID not set. Use set_sheet_id() method.")
            
            # Get all data from the sheet
            range_name = f"{sheet_name}!A:F"
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=range_name
            ).execute()
            
            values = result.get('values', [])
            
            if not values:
                return []
            
            # Skip header row and convert to list of dictionaries
            forms = []
            for index, row in enumerate(values[1:]):  # Skip header row
                form = parse_form_row(row, sheet_row=index + 2)  # +2: sheet rows are 1-based and row 1 is the header
                
                # Filter based on include_user_submitted flag
                if not include_user_submitted and form['user_submitted']:
                    continue
                
                forms.append(form)
            
            print(f"✅ Retrieved {len(forms)} forms from Google Sheet (include_user_submitted: {include_user_submitted})")
            return forms
            
        except HttpError as e:
            print(f"❌ HTTP Error getting forms: {e}")
            if raise_errors:
                raise
            return []
        except Exception as e:
            print(f"❌ Error getting forms: {str(e)}")
            if raise_errors:
                raise
            return []
    
    def form_exists(self, form_name: str, sheet_name: str = "forms") -> bool:
        """
        Check if a form with the given name already exists
        (ignores letter case and extra spaces: "Gen Z " counts as "gen z")
        
        Args:
            form_name: Name of the form to check
            sheet_name: Name of the sheet tab (default: "forms")
            
        Returns:
            bool: True if form exists, False otherwise

        Raises:
            Exception if the sheet can't be read (so we never add a duplicate "just in case")
        """
        wanted = normalize_form_name(form_name)
        forms = self.get_all_forms(sheet_name, include_user_submitted=True, raise_errors=True)
        return any(normalize_form_name(form['form_name']) == wanted for form in forms)
    
    def add_custom_form(self, form_name: str, group: str = "", sheet_name: str = "forms") -> bool:
        """
        Add a custom user-submitted form to the Google Sheet.
        Written as: id | name | "" | "" | group | TRUE  (columns A:F)
        
        Args:
            form_name: Name of the custom form (extra spaces are removed)
            group: Semantic group for column E ("" if unknown, "hidden" to hide it)
            sheet_name: Name of the sheet tab (default: "forms")
            
        Returns:
            bool: True if successful (or the form already existed), False otherwise
        """
        try:
            form_name = clean_form_name(form_name)
            if not form_name:
                print("⚠️ Refusing to add a custom form with an empty name")
                return False

            # DEBUG: Log the form being added
            print(f"🔍 DEBUG: Adding custom form - name: '{form_name}', group: '{group}'")
            
            # Check if form already exists (case/space-insensitive)
            if self.form_exists(form_name, sheet_name):
                print(f"⚠️ Form '{form_name}' already exists, skipping creation")
                return True
            
            # Add the custom form with user_submitted=True and empty Ukrainian name / description
            result = self.add_form(
                form_name=form_name,
                ukrainian_name="",
                description="",
                group=group or "",
                user_submitted=True,
                sheet_name=sheet_name
            )
            
            print(f"🔍 DEBUG: add_form result for '{form_name}': {result}")
            return result
            
        except Exception as e:
            print(f"❌ Error adding custom form: {str(e)}")
            return False

    def update_form_groups(self, groups_by_row: Dict[int, str], sheet_name: str = "forms") -> int:
        """
        Write semantic groups into column E ONLY (used by scripts/suggest_groups.py --apply).
        Nothing else in the row is touched.

        Args:
            groups_by_row: {sheet_row_number: "group text"} (sheet rows are 1-based, header is row 1)
            sheet_name: Name of the sheet tab (default: "forms")

        Returns:
            int: number of cells updated
        """
        if not self.sheet_id:
            raise ValueError("Sheet ID not set. Use set_sheet_id() method.")
        if not groups_by_row:
            return 0

        data = []
        for sheet_row, group in sorted(groups_by_row.items()):
            if int(sheet_row) < 2:
                raise ValueError(f"Refusing to write into the header row (row {sheet_row})")
            data.append({
                'range': f"{sheet_name}!E{int(sheet_row)}",
                'values': [[group]]
            })

        result = self.service.spreadsheets().values().batchUpdate(
            spreadsheetId=self.sheet_id,
            body={
                'valueInputOption': 'RAW',
                'data': data
            }
        ).execute()

        updated = result.get('totalUpdatedCells', len(data))
        print(f"✅ Updated {updated} group cells in column E")
        return updated
    
    def update_form(self, row_number: int, form_name: str, form_description: str, category: str = "", sheet_name: str = "forms") -> bool:
        """
        Update a form in the Google Sheet
        
        Args:
            row_number: Row number to update (1-indexed, excluding header)
            form_name: New form name
            form_description: New form description
            category: New category
            sheet_name: Name of the sheet tab
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if not self.sheet_id:
                raise ValueError("Sheet ID not set. Use set_sheet_id() method.")
            
            # Calculate actual row number (add 1 for header row)
            actual_row = row_number + 1
            
            # Prepare the data
            values = [[form_name, form_description, category]]
            
            body = {
                'values': values
            }
            
            # Update the specific row
            range_name = f"{sheet_name}!A{actual_row}:C{actual_row}"
            result = self.service.spreadsheets().values().update(
                spreadsheetId=self.sheet_id,
                range=range_name,
                valueInputOption='RAW',
                body=body
            ).execute()
            
            print(f"✅ Form at row {row_number} updated in Google Sheet")
            return True
            
        except HttpError as e:
            print(f"❌ HTTP Error updating form: {e}")
            return False
        except Exception as e:
            print(f"❌ Error updating form: {str(e)}")
            return False
    
    def delete_form(self, row_number: int, sheet_name: str = "Sheet1") -> bool:
        """
        Delete a form from the Google Sheet
        
        Args:
            row_number: Row number to delete (1-indexed, excluding header)
            sheet_name: Name of the sheet tab
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if not self.sheet_id:
                raise ValueError("Sheet ID not set. Use set_sheet_id() method.")
            
            # Calculate actual row number (add 1 for header row)
            actual_row = row_number + 1
            
            # First, get the sheet properties to find the sheet ID
            sheet_metadata = self.service.spreadsheets().get(spreadsheetId=self.sheet_id).execute()
            sheets = sheet_metadata.get('sheets', '')
            sheet_id = None
            
            for sheet in sheets:
                if sheet['properties']['title'] == sheet_name:
                    sheet_id = sheet['properties']['sheetId']
                    break
            
            if sheet_id is None:
                raise ValueError(f"Sheet '{sheet_name}' not found")
            
            # Delete the row
            requests = [{
                'deleteDimension': {
                    'range': {
                        'sheetId': sheet_id,
                        'dimension': 'ROWS',
                        'startIndex': actual_row - 1,  # 0-indexed for API
                        'endIndex': actual_row
                    }
                }
            }]
            
            body = {
                'requests': requests
            }
            
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.sheet_id,
                body=body
            ).execute()
            
            print(f"✅ Form at row {row_number} deleted from Google Sheet")
            return True
            
        except HttpError as e:
            print(f"❌ HTTP Error deleting form: {e}")
            return False
        except Exception as e:
            print(f"❌ Error deleting form: {str(e)}")
            return False
  
    def get_prompt_from_sheet(self, sheet_name: str = "prompt", prompt_id: str = "1") -> Optional[str]:

        try:
            if not self.sheet_id:
                raise ValueError("Sheet ID not set. Use set_sheet_id() method.")
            
            # Get all data from the prompts sheet
            range_name = f"{sheet_name}!A:D"  # Columns A-D: ID, Prompt, Data, Version
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=range_name
            ).execute()
            
            values = result.get('values', [])
            
            if not values:
                print(f"❌ No data found in {sheet_name}")
                return None
            
            # Skip header row and find the prompt with the specified ID
            for row in values[1:]:  # Skip header row
                if len(row) >= 2:  # Make sure we have at least ID and Prompt columns
                    row_id = row[0] if len(row) > 0 else ""
                    prompt_text = row[1] if len(row) > 1 else ""
                    
                    # Match the ID (convert both to string for comparison)
                    if str(row_id).strip() == str(prompt_id).strip():
                        print(f"✅ Found prompt with ID '{prompt_id}' in {sheet_name}")
                        return prompt_text.strip()
            
            print(f"❌ Prompt with ID '{prompt_id}' not found in {sheet_name}")
            return None
            
        except HttpError as e:
            print(f"❌ HTTP Error getting prompt: {e}")
            return None
        except Exception as e:
            print(f"❌ Error getting prompt: {str(e)}")
            return None

    def get_history_data(self, sheet_name: str = "history") -> List[Dict[str, str]]:
        """
        Get all history data from the Google Sheet, sorted by newest first
        
        Args:
            sheet_name: Name of the history sheet tab (default: "history")
            
        Returns:
            List of dictionaries containing history data, sorted by datetime descending
        """
        try:
            if not self.sheet_id:
                raise ValueError("Sheet ID not set. Use set_sheet_id() method.")
            
            # Get all data from the history sheet
            # Columns: A=id, B=stars_count, C=source_form, D=source_form_id, 
            #         E=source_text, F=target_form, G=target_form_id, H=target_text, I=datetime
            range_name = f"{sheet_name}!A:I"
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=range_name
            ).execute()
            
            values = result.get('values', [])
            
            if not values:
                print(f"❌ No data found in {sheet_name}")
                return []
            
            # Skip header row and convert to list of dictionaries
            history_items = []
            for row in values[1:]:  # Skip header row
                # Handle rows with missing columns - ensure we have at least the essential columns
                if len(row) >= 6:  # At minimum we need id, source_form, source_text, target_form, target_text, datetime
                    history_item = {
                        'id': row[0] if len(row) > 0 else "",
                        'stars_count': int(row[1]) if len(row) > 1 and row[1].isdigit() else 0,
                        'source_form': row[2] if len(row) > 2 else "",
                        'source_form_id': row[3] if len(row) > 3 else "",
                        'source_text': row[4] if len(row) > 4 else "",
                        'target_form': row[5] if len(row) > 5 else "",
                        'target_form_id': row[6] if len(row) > 6 else "",
                        'target_text': row[7] if len(row) > 7 else "",
                        'datetime': row[8] if len(row) > 8 else ""
                    }
                    history_items.append(history_item)
            
            # Sort by datetime in descending order (newest first)
            # We'll parse the datetime strings to sort them properly
            from datetime import datetime as dt
            
            def parse_datetime(date_str):
                """Parse datetime string, return a datetime object for sorting"""
                if not date_str:
                    return dt.min  # Put empty dates at the end
                try:
                    # Try different datetime formats
                    for fmt in ['%m/%d/%Y', '%Y-%m-%d', '%m/%d/%Y %H:%M:%S', '%Y-%m-%d %H:%M:%S']:
                        try:
                            return dt.strptime(date_str, fmt)
                        except ValueError:
                            continue
                    # If all formats fail, try to parse ISO format
                    return dt.fromisoformat(date_str.replace('Z', '+00:00'))
                except:
                    return dt.min  # Put unparseable dates at the end
            
            # Sort by star count descending (highest first), then by datetime descending (newest first)
            history_items.sort(key=lambda x: (x['stars_count'], parse_datetime(x['datetime'])), reverse=True)
            
            print(f"✅ Retrieved {len(history_items)} history items from Google Sheet")
            return history_items
            
        except HttpError as e:
            print(f"❌ HTTP Error getting history data: {e}")
            return []
        except Exception as e:
            print(f"❌ Error getting history data: {str(e)}")
            return []

    def create_history_headers_if_needed(self, sheet_name: str = "history"):
        """
        Create headers in the history sheet if they don't exist
        Headers: id | stars_count | source_form | source_form_id | source_text | target_form | target_form_id | target_text | datetime
        """
        try:
            if not self.sheet_id:
                raise ValueError("Sheet ID not set. Use set_sheet_id() method.")
            
            # Check if headers exist
            range_name = f"{sheet_name}!A1:I1"
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=range_name
            ).execute()
            
            values = result.get('values', [])
            expected_headers = ['id', 'stars_count', 'source_form', 'source_form_id', 'source_text', 'target_form', 'target_form_id', 'target_text', 'datetime']
            
            # If no headers or wrong headers, create them
            if not values or values[0] != expected_headers:
                headers = [expected_headers]
                
                body = {
                    'values': headers
                }
                
                self.service.spreadsheets().values().update(
                    spreadsheetId=self.sheet_id,
                    range=f"{sheet_name}!A1:I1",
                    valueInputOption='RAW',
                    body=body
                ).execute()
                
                print(f"✅ History headers created in Google Sheet tab '{sheet_name}'")
            else:
                print(f"✅ History headers already exist in tab '{sheet_name}'")
                
        except HttpError as e:
            print(f"❌ HTTP Error creating history headers: {e}")
            raise
        except Exception as e:
            print(f"❌ Error creating history headers: {str(e)}")
            raise

    def add_translation_to_history(self, 
                                   source_form: str, 
                                   source_text: str, 
                                   target_form: str, 
                                   target_text: str,
                                   source_form_id: str = "",
                                   target_form_id: str = "",
                                   stars_count: int = 0,
                                   sheet_name: str = "history") -> bool:
        """
        Add a translation record to the history sheet
        
        Args:
            source_form: Name of the source form type
            source_text: Original text that was translated
            target_form: Name of the target form type
            target_text: The translated result
            source_form_id: ID of the source form (optional)
            target_form_id: ID of the target form (optional)
            stars_count: Number of stars/likes for this translation (default: 0)
            sheet_name: Name of the history sheet tab (default: "history")
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # DEBUG: Log what we're storing in history
            print(f"🔍 DEBUG: Sheets service - storing in history: sourceForm='{source_form}', targetForm='{target_form}'")
            
            if not self.sheet_id:
                raise ValueError("Sheet ID not set. Use set_sheet_id() method.")
            
            # Ensure history headers exist
            self.create_history_headers_if_needed(sheet_name)
            
            # Generate unique ID for this translation (timestamp-based)
            from datetime import datetime
            import uuid
            
            # Create a short unique ID
            translation_id = str(uuid.uuid4())[:8]
            
            # Format datetime as a readable string
            current_datetime = datetime.now().strftime("%m/%d/%Y %H:%M:%S")
            
            # Prepare the data to append
            # Columns: id | stars_count | source_form | source_form_id | source_text | target_form | target_form_id | target_text | datetime
            values = [[
                translation_id,
                stars_count,
                source_form,
                source_form_id,
                source_text,
                target_form,
                target_form_id,
                target_text,
                current_datetime
            ]]
            
            # DEBUG: Log the exact values being written to the sheet
            print(f"🔍 DEBUG: Writing to history sheet - values: {values[0]}")
            print(f"🔍 DEBUG: Specifically - source_form: '{source_form}', target_form: '{target_form}'")
            
            body = {
                'values': values
            }
            
            # Append the data to the history sheet
            result = self.service.spreadsheets().values().append(
                spreadsheetId=self.sheet_id,
                range=f"{sheet_name}!A:I",
                valueInputOption='RAW',
                insertDataOption='INSERT_ROWS',
                body=body
            ).execute()
            
            print(f"✅ Translation logged to history: {source_form} → {target_form} (ID: {translation_id})")
            return True
            
        except HttpError as e:
            print(f"❌ HTTP Error adding translation to history: {e}")
            return False
        except Exception as e:
            print(f"❌ Error adding translation to history: {str(e)}")
            return False

    def get_star_count(self, translation_id: str, sheet_name: str = "history") -> int:
        """
        Get the current star count for a translation
        
        Args:
            translation_id: The ID of the translation to get star count for
            sheet_name: The sheet name to read from (default: "history")
            
        Returns:
            int: Current star count (0 if translation not found)
        """
        try:
            if not self.service or not self.sheet_id:
                raise ValueError("Sheets service not initialized")
            
            # Get all data from the history sheet (only need columns A and B for star count)
            range_name = f"{sheet_name}!A:B"
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=range_name
            ).execute()
            
            values = result.get('values', [])
            if not values:
                return 0
            
            # Find the row with matching translation ID (column A)
            for i, row in enumerate(values):
                if len(row) > 0 and row[0] == translation_id:
                    # Star count is in column B (index 1)
                    if len(row) > 1:
                        try:
                            return int(row[1])
                        except (ValueError, TypeError):
                            return 0
                    return 0
            
            # Translation ID not found
            return 0
            
        except Exception as e:
            print(f"❌ Error getting star count for {translation_id}: {str(e)}")
            return 0

    def increment_star_count(self, translation_id: str, sheet_name: str = "history") -> int:
        """
        Increment the star count for a translation
        
        Args:
            translation_id: The ID of the translation to increment stars for
            sheet_name: The sheet name to update (default: "history")
            
        Returns:
            int: New star count after increment
        """
        try:
            if not self.service or not self.sheet_id:
                raise ValueError("Sheets service not initialized")
            
            # Get current star count
            current_count = self.get_star_count(translation_id, sheet_name)
            new_count = current_count + 1
            
            # Update the star count in the sheet
            self._update_star_count_in_sheet(translation_id, new_count, sheet_name)
            
            print(f"✅ Incremented star count for {translation_id}: {current_count} → {new_count}")
            return new_count
            
        except Exception as e:
            print(f"❌ Error incrementing star count for {translation_id}: {str(e)}")
            # Return current count as fallback
            return self.get_star_count(translation_id, sheet_name)

    def decrement_star_count(self, translation_id: str, sheet_name: str = "history") -> int:
        """
        Decrement the star count for a translation (minimum 0)
        
        Args:
            translation_id: The ID of the translation to decrement stars for
            sheet_name: The sheet name to update (default: "history")
            
        Returns:
            int: New star count after decrement
        """
        try:
            if not self.service or not self.sheet_id:
                raise ValueError("Sheets service not initialized")
            
            # Get current star count
            current_count = self.get_star_count(translation_id, sheet_name)
            new_count = max(0, current_count - 1)  # Don't go below 0
            
            # Update the star count in the sheet
            self._update_star_count_in_sheet(translation_id, new_count, sheet_name)
            
            print(f"✅ Decremented star count for {translation_id}: {current_count} → {new_count}")
            return new_count
            
        except Exception as e:
            print(f"❌ Error decrementing star count for {translation_id}: {str(e)}")
            # Return current count as fallback
            return self.get_star_count(translation_id, sheet_name)

    def _update_star_count_in_sheet(self, translation_id: str, new_count: int, sheet_name: str = "history"):
        """
        Helper method to update star count in the Google Sheet
        
        Args:
            translation_id: The ID of the translation to update
            new_count: The new star count value
            sheet_name: The sheet name to update
        """
        try:
            # Get all data from the history sheet (only need columns A and B)
            range_name = f"{sheet_name}!A:B"
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=range_name
            ).execute()
            
            values = result.get('values', [])
            if not values:
                raise ValueError(f"No data found in {sheet_name} sheet")
            
            # Find the row with matching translation ID
            for i, row in enumerate(values):
                if len(row) > 0 and row[0] == translation_id:
                    # Update star count in column B (index 1)
                    row_number = i + 1  # Sheets use 1-based indexing
                    update_range = f"{sheet_name}!B{row_number}"
                    
                    body = {
                        'values': [[new_count]]
                    }
                    
                    self.service.spreadsheets().values().update(
                        spreadsheetId=self.sheet_id,
                        range=update_range,
                        valueInputOption='RAW',
                        body=body
                    ).execute()
                    
                    return
            
            # If we get here, the translation ID wasn't found
            raise ValueError(f"Translation ID {translation_id} not found in {sheet_name} sheet")
            
        except Exception as e:
            print(f"❌ Error updating star count in sheet: {str(e)}")
            raise

    def create_interest_headers_if_needed(self, sheet_name: str = "interest_registered"):
        """
        Create headers in the interest tracking sheet if they don't exist
        Headers: id | what | counter
        """
        try:
            if not self.sheet_id:
                raise ValueError("Sheet ID not set. Use set_sheet_id() method.")
            
            # Check if headers exist
            range_name = f"{sheet_name}!A1:C1"
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=range_name
            ).execute()
            
            values = result.get('values', [])
            expected_headers = ['id', 'what', 'counter']
            
            # If no headers or wrong headers, create them
            if not values or values[0] != expected_headers:
                headers = [expected_headers]
                
                body = {
                    'values': headers
                }
                
                self.service.spreadsheets().values().update(
                    spreadsheetId=self.sheet_id,
                    range=f"{sheet_name}!A1:C1",
                    valueInputOption='RAW',
                    body=body
                ).execute()
                
                print(f"✅ Interest headers created in Google Sheet tab '{sheet_name}'")
            else:
                print(f"✅ Interest headers already exist in tab '{sheet_name}'")
                
        except HttpError as e:
            print(f"❌ HTTP Error creating interest headers: {e}")
            raise
        except Exception as e:
            print(f"❌ Error creating interest headers: {str(e)}")
            raise

    def initialize_interest_data_if_needed(self, sheet_name: str = "interest_registered"):
        """
        Initialize the interest tracking data with default entries if they don't exist
        Creates entries for 'images' and 'websites' with counter 0
        """
        try:
            if not self.sheet_id:
                raise ValueError("Sheet ID not set. Use set_sheet_id() method.")
            
            # Ensure headers exist first
            self.create_interest_headers_if_needed(sheet_name)
            
            # Get current data to check if initialization is needed
            range_name = f"{sheet_name}!A:C"
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=range_name
            ).execute()
            
            values = result.get('values', [])
            
            # Check if we have the required entries
            existing_entries = set()
            if len(values) > 1:  # Skip header row
                for row in values[1:]:
                    if len(row) >= 2:  # Make sure we have at least id and what columns
                        existing_entries.add(row[1].strip().lower())
            
            # Initialize missing entries
            required_entries = ['images', 'websites']
            missing_entries = []
            
            for entry in required_entries:
                if entry not in existing_entries:
                    missing_entries.append(entry)
            
            if missing_entries:
                # Prepare data to append
                rows_to_add = []
                for i, entry in enumerate(missing_entries):
                    # Use simple incremental IDs starting from current row count
                    current_id = len(values) + i  # This will give us the next available ID
                    rows_to_add.append([current_id, entry, 0])
                
                body = {
                    'values': rows_to_add
                }
                
                # Append the missing entries
                self.service.spreadsheets().values().append(
                    spreadsheetId=self.sheet_id,
                    range=f"{sheet_name}!A:C",
                    valueInputOption='RAW',
                    insertDataOption='INSERT_ROWS',
                    body=body
                ).execute()
                
                print(f"✅ Initialized interest data with entries: {missing_entries}")
            else:
                print(f"✅ Interest data already initialized with required entries")
                
        except HttpError as e:
            print(f"❌ HTTP Error initializing interest data: {e}")
            raise
        except Exception as e:
            print(f"❌ Error initializing interest data: {str(e)}")
            raise

    def increment_interest_counter(self, content_type: str, sheet_name: str = "interest_registered") -> int:
        """
        Increment the counter for a specific content type (images or websites)
        
        Args:
            content_type: The type of content ('images' or 'websites')
            sheet_name: The sheet name to update (default: "interest_registered")
            
        Returns:
            int: New counter value after increment
        """
        try:
            if not self.service or not self.sheet_id:
                raise ValueError("Sheets service not initialized")
            
            # Normalize content type to lowercase
            content_type = content_type.lower().strip()
            
            if content_type not in ['images', 'websites']:
                raise ValueError(f"Invalid content type '{content_type}'. Must be 'images' or 'websites'")
            
            # Ensure the sheet and data are properly initialized
            self.initialize_interest_data_if_needed(sheet_name)
            
            # Get all data from the interest sheet
            range_name = f"{sheet_name}!A:C"
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=range_name
            ).execute()
            
            values = result.get('values', [])
            if not values or len(values) <= 1:  # No data or only headers
                raise ValueError(f"No data found in {sheet_name} sheet")
            
            # Find the row with matching content type
            for i, row in enumerate(values):
                if i == 0:  # Skip header row
                    continue
                    
                if len(row) >= 2 and row[1].strip().lower() == content_type:
                    # Get current counter value
                    current_counter = 0
                    if len(row) >= 3:
                        try:
                            current_counter = int(row[2])
                        except (ValueError, TypeError):
                            current_counter = 0
                    
                    # Increment counter
                    new_counter = current_counter + 1
                    
                    # Update counter in column C
                    row_number = i + 1  # Sheets use 1-based indexing
                    update_range = f"{sheet_name}!C{row_number}"
                    
                    body = {
                        'values': [[new_counter]]
                    }
                    
                    self.service.spreadsheets().values().update(
                        spreadsheetId=self.sheet_id,
                        range=update_range,
                        valueInputOption='RAW',
                        body=body
                    ).execute()
                    
                    print(f"✅ Incremented {content_type} counter: {current_counter} → {new_counter}")
                    return new_counter
            
            # If we get here, the content type wasn't found
            raise ValueError(f"Content type '{content_type}' not found in {sheet_name} sheet")
            
        except Exception as e:
            print(f"❌ Error incrementing interest counter for {content_type}: {str(e)}")
            raise

    def get_interest_counter(self, content_type: str, sheet_name: str = "interest_registered") -> int:
        """
        Get the current counter value for a specific content type
        
        Args:
            content_type: The type of content ('images' or 'websites')
            sheet_name: The sheet name to read from (default: "interest_registered")
            
        Returns:
            int: Current counter value (0 if not found)
        """
        try:
            if not self.service or not self.sheet_id:
                raise ValueError("Sheets service not initialized")
            
            # Normalize content type to lowercase
            content_type = content_type.lower().strip()
            
            # Get all data from the interest sheet
            range_name = f"{sheet_name}!A:C"
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=range_name
            ).execute()
            
            values = result.get('values', [])
            if not values or len(values) <= 1:  # No data or only headers
                return 0
            
            # Find the row with matching content type
            for i, row in enumerate(values):
                if i == 0:  # Skip header row
                    continue
                    
                if len(row) >= 2 and row[1].strip().lower() == content_type:
                    # Get counter value
                    if len(row) >= 3:
                        try:
                            return int(row[2])
                        except (ValueError, TypeError):
                            return 0
                    return 0
            
            # Content type not found
            return 0
            
        except Exception as e:
            print(f"❌ Error getting interest counter for {content_type}: {str(e)}")
            return 0

    def create_feedback_headers_if_needed(self, sheet_name: str = "feedback"):
        """
        Create headers in the feedback sheet if they don't exist
        Headers: Id | Text | DateTime
        """
        try:
            if not self.sheet_id:
                raise ValueError("Sheet ID not set. Use set_sheet_id() method.")
            
            # Check if headers exist
            range_name = f"{sheet_name}!A1:C1"
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=range_name
            ).execute()
            
            values = result.get('values', [])
            expected_headers = ['Id', 'Text', 'DateTime']
            
            # If no headers or wrong headers, create them
            if not values or values[0] != expected_headers:
                headers = [expected_headers]
                
                body = {
                    'values': headers
                }
                
                self.service.spreadsheets().values().update(
                    spreadsheetId=self.sheet_id,
                    range=f"{sheet_name}!A1:C1",
                    valueInputOption='RAW',
                    body=body
                ).execute()
                
                print(f"✅ Feedback headers created in Google Sheet tab '{sheet_name}'")
            else:
                print(f"✅ Feedback headers already exist in tab '{sheet_name}'")
                
        except HttpError as e:
            print(f"❌ HTTP Error creating feedback headers: {e}")
            raise
        except Exception as e:
            print(f"❌ Error creating feedback headers: {str(e)}")
            raise

    def add_feedback(self, text: str, sheet_name: str = "feedback") -> bool:
        """
        Add feedback to the Google Sheet
        
        Args:
            text: The feedback text to add
            sheet_name: Name of the feedback sheet tab (default: "feedback")
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if not self.sheet_id:
                raise ValueError("Sheet ID not set. Use set_sheet_id() method.")
            
            # Ensure feedback headers exist
            self.create_feedback_headers_if_needed(sheet_name)
            
            # Generate unique ID for this feedback (timestamp-based)
            from datetime import datetime
            import uuid
            
            # Create a short unique ID
            feedback_id = str(uuid.uuid4())[:8]
            
            # Format datetime as a readable string
            current_datetime = datetime.now().strftime("%m/%d/%Y %H:%M:%S")
            
            # Prepare the data to append
            # Columns: Id | Text | DateTime
            values = [[feedback_id, text, current_datetime]]
            
            body = {
                'values': values
            }
            
            # Append the data to the feedback sheet
            result = self.service.spreadsheets().values().append(
                spreadsheetId=self.sheet_id,
                range=f"{sheet_name}!A:C",
                valueInputOption='RAW',
                insertDataOption='INSERT_ROWS',
                body=body
            ).execute()
            
            print(f"✅ Feedback added to sheet: {feedback_id} - {text[:50]}...")
            return True
            
        except HttpError as e:
            print(f"❌ HTTP Error adding feedback: {e}")
            return False
        except Exception as e:
            print(f"❌ Error adding feedback: {str(e)}")
            return False

    def create_interest_registered_headers_if_needed(self, sheet_name: str = "interest_registered"):
        """
        Create headers in the interest registered sheet if they don't exist
        Headers: id | what | datetime
        """
        try:
            if not self.sheet_id:
                raise ValueError("Sheet ID not set. Use set_sheet_id() method.")
            
            # Check if headers exist
            range_name = f"{sheet_name}!A1:C1"
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=range_name
            ).execute()
            
            values = result.get('values', [])
            expected_headers = ['id', 'what', 'datetime']
            
            # If no headers or wrong headers, create them
            if not values or values[0] != expected_headers:
                headers = [expected_headers]
                
                body = {
                    'values': headers
                }
                
                self.service.spreadsheets().values().update(
                    spreadsheetId=self.sheet_id,
                    range=f"{sheet_name}!A1:C1",
                    valueInputOption='RAW',
                    body=body
                ).execute()
                
                print(f"✅ Interest registered headers created in Google Sheet tab '{sheet_name}'")
            else:
                print(f"✅ Interest registered headers already exist in tab '{sheet_name}'")
                
        except HttpError as e:
            print(f"❌ HTTP Error creating interest registered headers: {e}")
            raise
        except Exception as e:
            print(f"❌ Error creating interest registered headers: {str(e)}")
            raise

    def add_interest_record(self, what: str, datetime_str: str, sheet_name: str = "interest_registered") -> str:
        """
        Add a new interest record to the sheet (creates a new row for each click)
        
        Args:
            what: The content type or action being tracked
            datetime_str: The timestamp for this record
            sheet_name: The sheet name to add to (default: "interest_registered")
            
        Returns:
            str: The generated record ID
        """
        try:
            if not self.service or not self.sheet_id:
                raise ValueError("Sheets service not initialized")
            
            # Ensure headers exist
            self.create_interest_registered_headers_if_needed(sheet_name)
            
            # Get current data to determine next ID
            range_name = f"{sheet_name}!A:C"
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=range_name
            ).execute()
            
            values = result.get('values', [])
            
            # Calculate next ID (start from 1 if no data, otherwise increment from max ID)
            next_id = 1
            if len(values) > 1:  # Skip header row
                max_id = 0
                for row in values[1:]:
                    if len(row) >= 1:
                        try:
                            current_id = int(row[0])
                            max_id = max(max_id, current_id)
                        except ValueError:
                            continue
                next_id = max_id + 1
            
            # Prepare the new record
            record_data = [[next_id, what, datetime_str]]
            
            body = {
                'values': record_data
            }
            
            # Append the new record to the sheet
            result = self.service.spreadsheets().values().append(
                spreadsheetId=self.sheet_id,
                range=f"{sheet_name}!A:C",
                valueInputOption='RAW',
                insertDataOption='INSERT_ROWS',
                body=body
            ).execute()
            
            print(f"✅ Interest record added: ID {next_id} - {what} at {datetime_str}")
            return str(next_id)
            
        except HttpError as e:
            print(f"❌ HTTP Error adding interest record: {e}")
            raise
        except Exception as e:
            print(f"❌ Error adding interest record: {str(e)}")
            raise

    def get_interest_timestamps(self, what: str, sheet_name: str = "interest_registered") -> List[str]:
        """
        The datetime texts of all interest rows for one kind of event (e.g. "image_converted").
        Used to count today's photo translations after a restart (see ImageRateLimiter.seed_daily_count).
        """
        if not self.service or not self.sheet_id:
            raise ValueError("Sheets service not initialized")

        result = self.service.spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A:C"
        ).execute()

        values = result.get('values', [])
        return [str(row[2]) for row in values[1:] if len(row) >= 3 and str(row[1]).strip() == what]


# Global instance - will be initialized in the routes
sheets_service = None

def get_sheets_service() -> SheetsService:
    """Get the global sheets service instance"""
    global sheets_service
    if sheets_service is None:
        sheets_service = SheetsService()
    return sheets_service

def initialize_sheets_service(sheet_id: str):
    """Initialize the global sheets service with a sheet ID"""
    global sheets_service
    sheets_service = SheetsService()
    sheets_service.set_sheet_id(sheet_id)
    return sheets_service
