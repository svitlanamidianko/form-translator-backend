"""
Google Sheets Service for Form Translator Backend

This module handles all Google Sheets operations for storing forms data.
The sheet structure is:
- Column A: Form Name
- Column B: Form Description  
- Column C: Category
"""

import os
import json
from typing import List, Dict, Optional
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

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
            # Load credentials from JSON file
            if not os.path.exists(self.credentials_file):
                raise FileNotFoundError(f"Credentials file not found: {self.credentials_file}")
            
            # Define the scope for Google Sheets API
            scopes = ['https://www.googleapis.com/auth/spreadsheets']
            
            # Create credentials object
            credentials = Credentials.from_service_account_file(
                self.credentials_file, 
                scopes=scopes
            )
            
            # Build the service
            self.service = build('sheets', 'v4', credentials=credentials)
            print("✅ Google Sheets service initialized successfully!")
            
        except Exception as e:
            print(f"❌ Failed to initialize Google Sheets service: {str(e)}")
            raise
    
    def set_sheet_id(self, sheet_id: str):
        """Set the Google Sheets ID"""
        self.sheet_id = sheet_id
        print(f"📊 Sheet ID set to: {sheet_id}")
    
    def create_headers_if_needed(self, sheet_name: str = "Sheet1"):
        """
        Create headers in the sheet if they don't exist
        Headers: Form Name | Form Description | Category
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
            
            # If no headers or wrong headers, create them
            if not values or values[0] != ['Form Name', 'Form Description', 'Category']:
                headers = [['Form Name', 'Form Description', 'Category']]
                
                body = {
                    'values': headers
                }
                
                self.service.spreadsheets().values().update(
                    spreadsheetId=self.sheet_id,
                    range=f"{sheet_name}!A1:C1",
                    valueInputOption='RAW',
                    body=body
                ).execute()
                
                print("✅ Headers created in Google Sheet")
            else:
                print("✅ Headers already exist")
                
        except HttpError as e:
            print(f"❌ HTTP Error creating headers: {e}")
            raise
        except Exception as e:
            print(f"❌ Error creating headers: {str(e)}")
            raise
    
    def add_form(self, form_name: str, form_description: str, category: str = "", sheet_name: str = "Sheet1") -> bool:
        """
        Add a new form to the Google Sheet
        
        Args:
            form_name: Name of the form
            form_description: Description of the form
            category: Category of the form (optional)
            sheet_name: Name of the sheet tab (default: "Sheet1")
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if not self.sheet_id:
                raise ValueError("Sheet ID not set. Use set_sheet_id() method.")
            
            # Ensure headers exist
            self.create_headers_if_needed(sheet_name)
            
            # Prepare the data to append
            values = [[form_name, form_description, category]]
            
            body = {
                'values': values
            }
            
            # Append the data to the sheet
            result = self.service.spreadsheets().values().append(
                spreadsheetId=self.sheet_id,
                range=f"{sheet_name}!A:C",
                valueInputOption='RAW',
                insertDataOption='INSERT_ROWS',
                body=body
            ).execute()
            
            print(f"✅ Form '{form_name}' added to Google Sheet")
            return True
            
        except HttpError as e:
            print(f"❌ HTTP Error adding form: {e}")
            return False
        except Exception as e:
            print(f"❌ Error adding form: {str(e)}")
            return False
    
    def get_all_forms(self, sheet_name: str = "Sheet1") -> List[Dict[str, str]]:
        """
        Get all forms from the Google Sheet
        
        Args:
            sheet_name: Name of the sheet tab (default: "Sheet1")
            
        Returns:
            List of dictionaries containing form data
        """
        try:
            if not self.sheet_id:
                raise ValueError("Sheet ID not set. Use set_sheet_id() method.")
            
            # Get all data from the sheet
            range_name = f"{sheet_name}!A:C"
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=range_name
            ).execute()
            
            values = result.get('values', [])
            
            if not values:
                return []
            
            # Skip header row and convert to list of dictionaries
            forms = []
            for row in values[1:]:  # Skip header row
                # Handle rows with missing columns
                form_name = row[0] if len(row) > 0 else ""
                form_description = row[1] if len(row) > 1 else ""
                category = row[2] if len(row) > 2 else ""
                
                forms.append({
                    'form_name': form_name,
                    'form_description': form_description,
                    'category': category
                })
            
            print(f"✅ Retrieved {len(forms)} forms from Google Sheet")
            return forms
            
        except HttpError as e:
            print(f"❌ HTTP Error getting forms: {e}")
            return []
        except Exception as e:
            print(f"❌ Error getting forms: {str(e)}")
            return []
    
    def update_form(self, row_number: int, form_name: str, form_description: str, category: str = "", sheet_name: str = "Sheet1") -> bool:
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
