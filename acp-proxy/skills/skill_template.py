# acp-proxy/skills/skill_template.py
import json
import logging
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

def process_json_input(json_string: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, str]]]:
    """
    Safe wrapper for JSON parsing with standardized error handling
    
    Args:
        json_string: Input JSON string to parse
        
    Returns:
        Tuple of (parsed_data, error_dict)
        error_dict is None on success, contains 'error' and 'detail' keys on failure
    """
    try:
        data = json.loads(json_string)
        return data, None
    except json.JSONDecodeError as e:
        error_msg = "Invalid JSON format"
        detail_msg = f"JSON parsing failed: {e}"
        logger.warning(f"{error_msg}: {detail_msg} | Input preview: {json_string[:100]}...")
        return None, {
            "error": error_msg,
            "detail": detail_msg
        }
    except ValueError as e:
        error_msg = "JSON value error"
        detail_msg = f"Value error during JSON parsing: {e}"
        logger.error(f"{error_msg}: {detail_msg}")
        return None, {
            "error": error_msg,
            "detail": detail_msg
        }
    except Exception as e:
        error_msg = "Unexpected JSON parsing error"
        detail_msg = f"Unexpected error: {type(e).__name__}: {e}"
        logger.error(f"{error_msg}: {detail_msg}", exc_info=True)
        return None, {
            "error": error_msg,
            "detail": "An unexpected error occurred during JSON parsing"
        }

# Example usage patterns for each skill file:

def example_skill_method_1(input_data: str) -> Dict[str, Any]:
    """Example showing original direct json.loads usage replaced with safe parsing"""
    # Original: data = json.loads(input_data)
    data, error = process_json_input(input_data)
    if error:
        return error
    
    # Continue with original logic using data
    # ... (original processing continues here)
    return {"success": True, "data": data}

def example_skill_method_2(config_json: str, params_json: str) -> Dict[str, Any]:
    """Example with multiple JSON parsing calls in one method"""
    # First JSON parse
    config, error = process_json_input(config_json)
    if error:
        return {"error": "Invalid configuration", "detail": error["detail"]}
    
    # Second JSON parse
    params, error = process_json_input(params_json)
    if error:
        return {"error": "Invalid parameters", "detail": error["detail"]}
    
    # Continue with original logic
    # ... (original processing continues here)
    return {"config": config, "params": params}

def example_skill_method_3(json_string: str, fallback: Any = None) -> Any:
    """Example showing non-dict return type scenario"""
    # For methods that don't return dicts
    data, error = process_json_input(json_string)
    if error:
        # If method can't return dict, handle differently
        # Option 1: Log and return fallback
        logger.error(f"JSON parsing failed: {error}")
        return fallback
        