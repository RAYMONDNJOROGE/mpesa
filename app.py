# app.py
import os
import base64
from datetime import datetime
import requests
import json
import pytz # Used for accurate timezone handling

from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv # For loading environment variables

# --- Flask Application Setup ---
app = Flask(__name__)

# Load environment variables from .env file.
# This makes sure your sensitive credentials are not hardcoded in the file.
load_dotenv()

# --- M-Pesa API Configuration ---
# Retrieve credentials and configurations securely from environment variables.
# **IMPORTANT**: In a production environment, ensure these are loaded directly
# from your server's environment, not just from a .env

# Transaction details that 

# Basic check to ensure all critical environment variables are loaded
if not all([CONSUMER_KEY, CONSUMER_SECRET, PASSKEY, BUSINESS_SHORT_CODE, CALLBACK_URL, OAUTH_URL, STKPUSH_URL]):
    app.logger.critical("CRITICAL ERROR: One or more M-Pesa environment variables are not set. Please check your .env file and server environment.")
    exit(1) # Exit if essential configurations are missing

# --- Helper Function: Get Safaricom Access Token ---
def get_access_token():
    """
    Fetches a new OAuth access token from Safaricom's Daraja API.
    This token is required for authenticating subsequent M-Pesa API calls.
    """
    try:
        # Concatenate consumer key and secret with a colon and Base64 encode it for Basic Auth
        auth_string = f"{CONSUMER_KEY}:{CONSUMER_SECRET}"
        encoded_auth_string = base64.b64encode(auth_string.encode('utf-8')).decode('utf-8')

        headers = {
            "Authorization": f"Basic {encoded_auth_string}",
            "Content-Type": "application/json"
        }
        
        # Make the GET request to the OAuth endpoint
        # `verify=True` ensures SSL certificate verification. Set to `False` for sandbox
        # if you encounter SSL errors, but always ensure proper verification in production.
        response = requests.get(OAUTH_URL, headers=headers, verify=True)
        response.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)
        
        result = response.json()
        access_token = result.get("access_token")

        if access_token:
            app.logger.info("Successfully obtained M-Pesa access token.")
            return access_token
        else:
            app.logger.error(f"Access token not found in response: {result}")
            return None

    except requests.exceptions.RequestException as e:
        app.logger.error(f"Network or API error while getting access token: {e}")
        return None
    except json.JSONDecodeError:
        app.logger.error(f"Failed to decode JSON from access token response: {response.text}")
        return None
    except Exception as e:
        app.logger.exception("An unexpected error occurred in get_access_token.")
        return None

# --- Helper Function: Generate M-Pesa Timestamp and Password ---
def generate_mpesa_credentials():
    """
    Generates the current timestamp in YYYYMMDDHHmmss format (EAT)
    and the corresponding M-Pesa password for STK Push.
    """
    # Safaricom requires the timestamp to be in East African Time (EAT)
    nairobi_tz = pytz.timezone('Africa/Nairobi')
    # Get the current time in Nairobi timezone and format it
    timestamp = datetime.now(nairobi_tz).strftime("%Y%m%d%H%M%S")

    # The M-Pesa password for STK Push is a Base64-encoded string derived from:
    # BusinessShortCode + Passkey + Timestamp
    password_str = f"{BUSINESS_SHORT_CODE}{PASSKEY}{timestamp}"
    password = base64.b64encode(password_str.encode('utf-8')).decode('utf-8')

    app.logger.info(f"Generated timestamp: {timestamp}, password: [HIDDEN]")
    return timestamp, password

# --- Flask Routes ---

@app.route('/')
def index():
    """
    Serves the main HTML payment form to the client.
    This assumes your HTML file is named 'index.html' and located in the 'templates' folder.
    """
    return render_template('index.html')

@app.route('/api/stkpush', methods=['POST'])
def initiate_stk_push():
    """
    Endpoint to handle incoming STK Push requests from the frontend.
    It validates inputs, gets an access token, generates credentials,
    and sends the STK Push request to Safaricom.
    """
    data = request.get_json()
    phone_number = data.get('phoneNumber')
    amount = data.get('amount')

    app.logger.info(f"Received STK Push request from frontend: Phone='{phone_number}', Amount='{amount}'")

    # --- Server-Side Input Validation ---
    # Crucial for security and preventing invalid requests to Safaricom
    if not phone_number or not amount:
        app.logger.warning("STK Push validation failed: Missing phone number or amount.")
        return jsonify({"success": False, "message": "Phone number and amount are required."}), 400

    # Validate phone number format for Safaricom (e.g., 2547XXXXXXXX or 2541XXXXXXXX)
    if not (isinstance(phone_number, str) and (phone_number.startswith("2547") or phone_number.startswith("2541")) and len(phone_number) == 12 and phone_number.isdigit()):
        app.logger.warning(f"STK Push validation failed: Invalid phone number format '{phone_number}'.")
        return jsonify({"success": False, "message": "Invalid Kenyan Safaricom phone number format. Must be 2547/2541XXXXXXXX."}), 400

    # Validate amount: must be a positive integer
    if not isinstance(amount, int) or amount <= 0:
        app.logger.warning(f"STK Push validation failed: Invalid amount '{amount}'.")
        return jsonify({"success": False, "message": "Amount must be a positive integer."}), 400

    try:
        # 1. Get Access Token from Safaricom
        access_token = get_access_token()
        if not access_token:
            app.logger.error("STK Push failed: Could not obtain M-Pesa access token.")
            return jsonify({"success": False, "message": "Failed to connect to M-Pesa. Please try again later."}), 500

        # 2. Generate Dynamic Timestamp and Password
        timestamp, password = generate_mpesa_credentials()

        # 3. Construct the STK Push Payload as per Safaricom Daraja API documentation
        payload = {
            "BusinessShortCode": int(BUSINESS_SHORT_CODE), # Ensure it's an integer
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": TRANSACTION_TYPE,
            "Amount": amount,
            "PartyA": int(phone_number), # Customer's phone number initiating the payment
            "PartyB": int(BUSINESS_SHORT_CODE), # Your short code (Paybill/Till)
            "PhoneNumber": int(phone_number), # Customer's phone number again (redundant but required by M-Pesa)
            "CallBackURL": CALLBACK_URL, # Your backend endpoint to receive transaction status
            "AccountReference": ACCOUNT_REFERENCE, # Your unique reference for this transaction
            "TransactionDesc": TRANSACTION_DESC # Description of the transaction
        }

        # Set headers for the STK Push request
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        app.logger.info(f"Sending STK Push request to Safaricom with payload: {json.dumps(payload, indent=2)}")

        # Make the POST request to Safaricom's STK Push endpoint
        response = requests.post(STKPUSH_URL, json=payload, headers=headers, verify=True)
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)

        stk_response = response.json()
        app.logger.info(f"Received STK Push response from Safaricom: {json.dumps(stk_response, indent=2)}")

        # Check Safaricom's specific ResponseCode for successful STK Push initiation
        # ResponseCode '0' means the request was successfully received by Safaricom
        if stk_response.get("ResponseCode") == "0":
            return jsonify({
                "success": True,
                "message": stk_response.get("CustomerMessage", "STK Push initiated successfully! Please check your phone for the M-Pesa prompt."),
                "CheckoutRequestID": stk_response.get("CheckoutRequestID"), # Important for tracking
                "ResponseCode": stk_response.get("ResponseCode")
            }), 200
        else:
            # Handle cases where M-Pesa API itself returns an error for the request
            error_message = stk_response.get("ResponseDescription", "STK Push initiation failed due to an unknown M-Pesa error.")
            app.logger.error(f"STK Push failed at Safaricom: {error_message} | Full Response: {json.dumps(stk_response)}")
            return jsonify({
                "success": False,
                "message": error_message,
                "MpesaResponse": stk_response # Include full M-Pesa response for debugging
            }), 400

    except requests.exceptions.RequestException as e:
        app.logger.error(f"Network or API communication error during STK Push: {e}")
        return jsonify({"success": False, "message": f"Network or API communication error: {e}"}), 500
    except json.JSONDecodeError:
        app.logger.error(f"Failed to decode JSON from STK Push response: {response.text}")
        return jsonify({"success": False, "message": "Failed to parse M-Pesa API response."}), 500
    except Exception as e:
        app.logger.exception("An unexpected server error occurred during STK Push initiation.")
        return jsonify({"success": False, "message": f"An unexpected server error occurred: {e}"}), 500

@app.route('/api/mpesa_callback', methods=['POST'])
def mpesa_callback():
    """
    This is the M-Pesa Confirmation Callback URL endpoint.
    Safaricom sends transaction status updates (success/failure) to this endpoint
    after the customer interacts with the STK Push prompt on their phone.
    """
    try:
        callback_data = request.get_json()
        app.logger.info(f"Received M-Pesa Callback: {json.dumps(callback_data, indent=2)}")

        # Extract relevant information from the callback structure
        stk_callback = callback_data.get("Body", {}).get("stkCallback", {})
        result_code = stk_callback.get("ResultCode")
        checkout_request_id = stk_callback.get("CheckoutRequestID")
        result_description = stk_callback.get("ResultDesc")

        if result_code == 0:
            # Payment was successful!
            app.logger.info(f"M-Pesa STK Push successful for CheckoutRequestID: {checkout_request_id}")
            
            # Extract detailed transaction data from CallbackMetadata
            callback_metadata = stk_callback.get("CallbackMetadata", {})
            item_details = {}
            for item in callback_metadata.get("Item", []):
                item_details[item.get("Name")] = item.get("Value")

            mpesa_receipt_number = item_details.get("MpesaReceiptNumber")
            amount = item_details.get("Amount")
            transaction_date = item_details.get("TransactionDate")
            phone_number = item_details.get("PhoneNumber")

            app.logger.info(f"Payment Details: Receipt={mpesa_receipt_number}, Amount={amount}, "
                            f"Phone={phone_number}, Date={transaction_date}")

            # --- YOUR APPLICATION-SPECIFIC LOGIC HERE FOR SUCCESSFUL PAYMENT ---
            # 1. **VERIFY**: Look up the order/transaction using `CheckoutRequestID` in your database.
            # 2. **VALIDATE**: Ensure the `Amount` received matches the expected amount for the order.
            # 3. **UPDATE**: Mark the order/transaction as 'paid' or 'completed' in your database.
            # 4. **FULFILL**: Trigger any post-payment actions (e.g., send confirmation email/SMS, provision service, update inventory).
            # -------------------------------------------------------------------

        else:
            # Payment failed, was cancelled by the user, or other non-success code
            app.logger.warning(f"M-Pesa STK Push Failed/Cancelled: ResultCode='{result_code}', "
                               f"ResultDesc='{result_description}', CheckoutRequestID='{checkout_request_id}'")
            # --- YOUR APPLICATION-SPECIFIC LOGIC HERE FOR FAILED PAYMENT ---
            # 1. **UPDATE**: Mark the order/transaction as 'failed' or 'cancelled' in your database.
            # 2. **NOTIFY**: Inform the user about the payment failure.
            # -------------------------------------------------------------------

        # Safaricom expects a specific JSON response to acknowledge successful receipt of the callback.
        # This is CRUCIAL to prevent Safaricom from repeatedly sending the same callback.
        return jsonify({"ResultCode": 0, "ResultDesc": "Callback received successfully"}), 200

    except Exception as e:
        app.logger.exception("An unexpected error occurred while processing M-Pesa callback.")
        # If an error occurs during processing on your side,
        # you can return a non-zero ResultCode to indicate failure to Safaricom,
        # though often a 200 OK with any content is sufficient for them to stop retrying.
        return jsonify({"ResultCode": 1, "ResultDesc": f"Failed to process callback: {e}"}), 500

# --- Running the Flask App ---
if __name__ == '__main__':
    # Set Flask's debug mode based on FLASK_DEBUG environment variable.
    # debug=True enables automatic reloader and debugger.
    # **DO NOT USE debug=True IN PRODUCTION ENVIRONMENTS.**
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", port=5000)
