import json
import requests
import base64
import time
import os
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, ValidationError

# --- CONFIGURATION (Reads from render Environment Variables) ---
# The API_KEY is read securely from the environment variable named "API_KEY" set in render.
API_KEY = os.environ.get("API_KEY", "") 
GEMINI_MODEL = "gemini-2.5-flash-preview-09-2025"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={API_KEY}"
# --- END CONFIGURATION ---


# --- PYDANTIC SCHEMA DEFINITIONS ---

class BillItem(BaseModel):
    """Defines a single extracted line item (The required output structure)."""
    item_name: str = Field(..., description="Exactly as mentioned in the bill.")
    item_amount: float = Field(..., description="Net Amount of the item post discounts, as mentioned in the bill.")
    item_rate: float = Field(..., description="Exactly as mentioned in the bill, unit price or rate.")
    item_quantity: float = Field(..., description="Exactly as mentioned in the bill, quantity or count.")

class PagewiseLineItem(BaseModel):
    """Defines the line items extracted from a single page."""
    page_no: str = Field(..., description="The page number in the document (e.g., '1', '2').")
    page_type: str = Field(..., description="Classification of the page: Bill Detail, Final Bill, or Pharmacy.")
    bill_items: List[BillItem] = Field(..., description="List of all extracted line items on this page.")

class LLMExtractionOutput(BaseModel):
    """Internal schema to strictly guide the LLM's JSON output."""
    pagewise_line_items: List[PagewiseLineItem]
    document_final_total: float = Field(..., description="The final, grand total amount written on the entire bill document.")
    
class TokenUsage(BaseModel):
    total_tokens: int = Field(..., description="Cumulative Tokens from all LLM calls.")
    input_tokens: int = Field(..., description="Cumulative Input Tokens from all LLM calls.")
    output_tokens: int = Field(..., description="Cumulative Output Tokens from all LLM calls.")

class ExtractionData(BaseModel):
    pagewise_line_items: List[PagewiseLineItem]
    final_total_extracted: float = Field(..., description="**SUM OF ALL individual line item_amount entries.** This is the core calculation.")
    total_item_count: int
    sub_total_extracted: Optional[float] = None

class ExtractionResponse(BaseModel):
    """The final required output structure."""
    is_success: bool
    token_usage: TokenUsage
    data: ExtractionData

class ExtractionRequest(BaseModel):
    """The required input request body."""
    document: str


# --- HELPER FUNCTIONS ---

def _download_file_to_base64(url: str) -> str:
    """Downloads the file and encodes it as Base64 for the Gemini API."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        content_type = response.headers.get('Content-Type', 'image/jpeg')
        if not content_type.startswith('image/') and not content_type.startswith('application/pdf'):
            raise ValueError(f"Unsupported file type: {content_type}")
            
        base64_data = base64.b64encode(response.content).decode('utf-8')
        
        # Prepend the MIME type for the API
        return f"data:{content_type};base64,{base64_data}"
        
    except requests.exceptions.RequestException as e:
        # Raise an HTTPException if the download fails (404, 403, network error)
        error_detail = f"DOCUMENT DOWNLOAD FAILED: URL {url[:50]}... returned error: {e}"
        print(f"DEBUG ERROR: {error_detail}")
        raise HTTPException(status_code=400, detail=error_detail)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


async def extract_data_with_llm(document_url: str) -> Dict[str, Any]:
    """Calls the Gemini API using the multimodal document and strict JSON schema."""
    # Check 1: API Key existence
    if not API_KEY:
        raise HTTPException(status_code=500, detail="API_KEY is missing in Render environment variables. Check Project Settings.")

    # 1. Download and encode the file 
    base64_file_with_mime = _download_file_to_base64(document_url)
    mime_type, base64_data = base64_file_with_mime.split(',', 1)
    mime_type = mime_type.split(':')[1].split(';')[0]
    
    # 2. Define the LLM instruction prompt
    system_prompt = (
        "You are a highly accurate invoice data extraction specialist. "
        "Analyze the entire multi-page bill document and extract ALL line item details, quantities, rates, and amounts. "
        "Strictly adhere to the provided JSON schema for the output. "
        "The 'page_type' must be one of: 'Bill Detail', 'Final Bill', or 'Pharmacy'. "
        "The 'document_final_total' must be the exact grand total amount written on the entire bill document."
    )
    
    # 3. Construct the API payload
    payload = {
        "contents": [{
            "parts": [
                {"text": system_prompt},
                {"inlineData": {"mimeType": mime_type, "data": base64_data}}
            ]
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": json.loads(LLMExtractionOutput.model_json_schema()) 
        },
        "systemInstruction": {"parts": [{"text": system_prompt}]}
    }
    
    # 4. Make the API call with exponential backoff for robustness
    max_retries = 3
    response = None
    for attempt in range(max_retries):
        try:
            response = requests.post(
                GEMINI_API_URL, 
                headers={'Content-Type': 'application/json'}, 
                data=json.dumps(payload),
                timeout=120 
            )
            response.raise_for_status() 
            
            result = response.json()
            
            # 5. Extract JSON and Token Usage
            extracted_json_text = result['candidates'][0]['content']['parts'][0]['text']
            extracted_data = json.loads(extracted_json_text)
            
            usage_metadata = result.get('usageMetadata', {})
            input_tokens = usage_metadata.get('promptTokenCount', 0)
            output_tokens = usage_metadata.get('candidatesTokenCount', 0)
            
            return {
                "extracted_data": extracted_data,
                "token_usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens
                }
            }

        except requests.exceptions.RequestException as e:
            if response is not None and response.status_code == 429 and attempt < max_retries - 1:
                # Handle rate limiting with exponential backoff
                delay = 2 ** attempt
                time.sleep(delay)
                continue
            
            # Catch authentication errors (400, 403) and forward them
            error_detail = response.json().get('error', {}).get('message', 'Unknown API Error') if response is not None else str(e)
            raise HTTPException(status_code=response.status_code if response is not None else 500, detail=f"LLM API Error: {error_detail}")
        except (KeyError, IndexError, json.JSONDecodeError, AttributeError) as e:
            error_detail = f"LLM returned invalid or unexpected structure: {e}"
            raise HTTPException(status_code=500, detail=error_detail)
        
    raise HTTPException(status_code=503, detail="LLM API failed after multiple retries.")


# --- API INSTANCE AND ENDPOINT ---

app = FastAPI(title="HackRx Bill Extraction API", version="1.0.0")

@app.post("/extract-bill-data", response_model=ExtractionResponse, status_code=200)
async def extract_bill_data(request: ExtractionRequest):
    """
    Processes a document URL, extracts line items, calculates final totals, and returns the structured JSON.
    """
    document_url = request.document
    
    try:
        # Step 1: Call the LLM/IDP Service
        llm_output = await extract_data_with_llm(document_url)
        
        # Pydantic validation ensures the structure is correct here
        extracted_data = LLMExtractionOutput(**llm_output["extracted_data"])
        token_usage_counts = llm_output["token_usage"]

        # Step 2: Post-processing, Aggregation, and Validation (CRITICAL LOGIC)
        
        cumulative_extracted_total: float = 0.0
        cumulative_item_count: int = 0
        pagewise_output: List[PagewiseLineItem] = []
        
        # This loop guarantees the final total is the sum of extracted line items
        for page in extracted_data.pagewise_line_items:
            page_sum = 0.0
            
            # Accumulate sum and count
            for item in page.bill_items:
                page_sum += item.item_amount
                cumulative_item_count += 1

            # Accumulate the sum of line items across all pages
            cumulative_extracted_total += page_sum
            
            pagewise_output.append(page) # Append the validated page data

        # Step 3: Construct the final response
        response_data = ExtractionResponse(
            is_success=True,
            token_usage=TokenUsage(**token_usage_counts),
            data=ExtractionData(
                pagewise_line_items=pagewise_output,
                final_total_extracted=round(cumulative_extracted_total, 2), # FINAL REQUIRED CALCULATION
                total_item_count=cumulative_item_count,
            )
        )
        return response_data

    except HTTPException as e:
        # This catches all deliberate errors (API Key missing, Download failure, LLM auth errors)
        raise e
    
    except Exception as e:
        # Catch all other truly unexpected internal server errors
        print(f"UNCAUGHT INTERNAL SERVER ERROR: {str(e)}")
        # Raise a 500 error with the type of error for debugging
        raise HTTPException(
            status_code=500,
            detail=f"UNCAUGHT SERVER ERROR: {e.__class__.__name__}. Check Render logs."
        )
