import re

class InputDenoiser:
    """Compresses the size of inputs to llm for memory extraction 
        by compressing codes(language logics) and removing unnecssary
        things from error logs."""

    def __init__(self):
        # --- 1. LOG SIGNALS (The "Story" Markers) ---
        # Catches: Timestamps, Log Levels, Stack Traces, HTTP Methods, File Paths    
        self.log_signals = re.compile(
            r'^\s*('
            r'\d{4}[-/]\d{2}[-/]\d{2}|'         # ISO Dates
            r'\[?\d{2}:\d{2}:\d{2}|'            # Times
            r'[A-Z][a-z]{2}\s+\d{1,2}|'         # Syslog Dates
            r'\[?(INFO|WARN|ERR|DEBUG|FATAL|CRITICAL|TRACE)|' # Levels
            r'Traceback \(|File\s+[\'"]|at\s+[\w\.]+\(|Caused by:|' # Stack Traces
            r'Exception:|Error:|Panic:|Goroutine|' # Errors
            r'GET\s+|POST\s+|PUT\s+|DELETE\s+|'    # HTTP
            r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}' # IPv4
            r')', 
            re.IGNORECASE
        )

        # --- 2. CODE SIGNALS ---
        # Added quotes (["']) to catch docstrings/strings as code
        self.code_signals = re.compile(
            r'^\s*('
            r'if|else|elif|for|while|do|switch|case|default|break|return|' # Control
            r'try|except|catch|finally|throw|raise|await|async|' # Error/Async
            r'def|class|function|interface|struct|enum|print|printf|sys|import|from|#include|' # Defs
            r'var|let|const|public|private|int|float|void|string|' # Types
            r'}|{|\]|\[|\)|//|#\s|/\*|["\']|' # Syntax & Quotes
            r'[\w\._]+\s*=\s*' # Assignments
            r')\b', 
            re.IGNORECASE
        )
        self.import_pattern = re.compile(
            r'^\s*(import|from|#include|using|package|library)\b', 
            re.IGNORECASE
        )

        # 2. Decorators (Python @stuff, Java @Annotations)
        # We want to keep these attached to the function they decorate.
        self.decorator_pattern = re.compile(r'^\s*@')

        # 3. Global Constants (Heuristic: ALL_CAPS_VAR = value)
        # Matches "MAX_RETRIES =", "const int TIMEOUT =", etc.
        self.global_var_pattern = re.compile(r'^\s*([A-Z_][A-Z0-9_]*|const\s+.*)\s*=')

        # 4. Definitions: Keywords that start a structure
        # Added: template, enum, union, namespace for C++ coverage
        self.def_start_pattern = re.compile(
            r'^\s*(def|class|function|async|public|private|protected|void|struct|interface|enum|union|template|namespace)\b', 
            re.IGNORECASE
        )
        
        self.block_open_pattern = re.compile(r'.*?(:|\{)\s*$')
        self.header_pattern = re.compile(
            r'^\s*(\[?\d{4}-\d{2}-\d{2}|\[?\d{2}:\d{2}:\d{2}|[A-Z][a-z]{2}\s+\d+|\[?(INFO|WARN|ERROR|DEBUG|FATAL|TRACE|EPOCH))', 
            re.IGNORECASE
        )

        self.mask_map = [
            (re.compile(r'\b[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}\b'), '<UUID>'),
            (re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'), '<IP>'),
            (re.compile(r'\b0x[0-9a-fA-F]+\b'), '<MEM>'),
            # Alphanumeric IDs (e.g., User123, Session_99) - Must have 1 digit & 1 letter
            (re.compile(r'\b(?=[a-zA-Z]*\d)[a-zA-Z0-9_]+\b'), '<ID>'), 
            (re.compile(r'(["\']).*?\1'), '<STR>'),   # Quoted strings
            (re.compile(r'\[.*?\]'), '<META>'),       # Content in brackets
            (re.compile(r'\b\d+\b'), '<NUM>')         # Standalone digits
        ]

    def compress(self, text: str) -> str:
        """
        Tri-State Router: Splits input into CODE, LOG, and TEXT.
        Includes 'English Detection' to prevent user notes from being treated as code/logs.
        """
        if not text: return ""
        
        blocks = []
        lines = text.split('\n')
        current_block = []
        current_type = None 

        # Regex for "Plain English Sentence"
        # Starts with Capital letter, contains spaces, ends with punctuation, 
        # avoids obvious code symbols like { } ; =
        english_sentence = re.compile(r'^[A-Z][^\{\}\;\=\(\)]+\s[^\{\}\;\=\(\)]+[\.\?\!:]$')

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # 1. Analyze Signals
            is_log = bool(self.log_signals.match(stripped))
            is_code = bool(self.code_signals.match(stripped))
            is_text = bool(english_sentence.match(stripped))
            is_indented = len(line) - len(line.lstrip()) > 0
            
            new_type = None
            
            if is_log:
                new_type = 'LOG'
            elif is_code:
                new_type = 'CODE'
            elif is_text:
                new_type = 'TEXT' 
            
            if new_type is None:
                if current_type == 'LOG' and is_indented:
                    new_type = 'LOG'
                elif current_type == 'CODE' and is_indented:
                    new_type = 'CODE'
                else:
                    new_type = 'TEXT'

            # 4. State Change Detection
            if new_type != current_type and current_type is not None:
                blocks.append((current_type, '\n'.join(current_block)))
                current_block = []
            
            current_type = new_type
            current_block.append(line)

        # Flush final block
        if current_block:
            blocks.append((current_type if current_type else 'TEXT', '\n'.join(current_block)))

        # If there is only one block and it is of type 'TEXT', return the raw text directly
        if len(blocks) == 1 and blocks[0][0] == 'TEXT':
            return text.strip()

        # --- PHASE 2: PROCESSING ---
        final_output = []
        
        for type_, content in blocks:
            if not content.strip(): continue
            
            try:
                processed = ""
                header = ""
                
                if type_ == 'LOG':
                    processed = self.error_log_compressor(content)
                    header = "--- [LOG SEGMENT] ---"
                elif type_ == 'CODE':
                    processed = self.code_compressor(content)
                    header = "--- [CODE SEGMENT] ---"
                else:
                    processed = content.strip()
                    header = ""
                if processed:
                    final_output.append(f"{header}\n{processed}")

            except Exception as e:
                final_output.append(f"\n--- [RAW SEGMENT (Error: {str(e)})] ---\n{content}")
        
        output = '\n'.join(final_output).strip()
        if output:
            return output
        else:
            return text
     

    def code_compressor(self, code_text: str) -> str:
        """
        State-Machine Skeletonizer
        1. Handles Multi-line comments (Python '''/\"\"\", C++ /*...*/).
        2. Handles Multi-line signatures (Args on new lines, Allman style braces).
        3. Preserves structure while compressing logic.
        """
        lines = code_text.split('\n')
        compressed_lines = []
        
        in_comment_block = False
        comment_ender = ""  # Tracks if we are waiting for ''' or """ or */
        
        in_signature = False # Are we currently reading a multi-line function def?
        hide_level = [-1]      # Indentation level to hide (Logic Body)
        
        for line in lines:
            stripped = line.strip()
            
            # --- 1. HANDLE COMMENTS (Priority #1) ---
            # Python Triple Quotes
            if '"""' in line or "'''" in line:
                count = line.count('"""') + line.count("'''")
                if count % 2 == 1: # If odd number, we toggled state
                    if in_comment_block:
                        compressed_lines.append(line)
                    in_comment_block = not in_comment_block
            
            # C-Style Block Comments (/* ... */)
            if '/*' in line and '*/' not in line:
                in_comment_block = True
                comment_ender = '*/'
            elif '*/' in line and in_comment_block and comment_ender == '*/':
                in_comment_block = False
                compressed_lines.append(line) # Keep the closing line
                continue

            # Single Line Comments (Always keep)
            if "#" in stripped or "//" in stripped:
                compressed_lines.append(line)
                continue
                
            # IF INSIDE COMMENT BLOCK: Keep everything, ignore logic hiding
            if in_comment_block:
                compressed_lines.append(line)
                continue

            # --- 2. HANDLE CODE STRUCTURE ---

            current_indent = len(line) - len(line.lstrip())
            # PRIORITY CHECK: Imports & Decorators
            # We always want these, even if we are theoretically "hiding" (though usually these are top level)
            if self.import_pattern.match(stripped) or self.decorator_pattern.match(stripped):
                compressed_lines.append(line)
                continue

            # CHECK: Multi-line signature continuation?
            if in_signature:
                compressed_lines.append(line)
                if self.block_open_pattern.match(stripped) or stripped == "{":
                    in_signature = False
                    hide_level.append(current_indent)
                continue

            # CHECK: New Definition Start?
            if self.def_start_pattern.match(stripped):
                compressed_lines.append(line)
                
                # Case A: Signature ends on this line ("def foo():" or "void foo() {")
                if self.block_open_pattern.match(stripped) or stripped.endswith("{") or stripped.endswith(":"):
                    in_signature = False
                    hide_level.append(current_indent)
                
                # Case B: It is a C++ style definition "int main(" -> Wait for '{'
                elif "(" in stripped: 
                    in_signature = True
                
                # Case C: It matches a type (e.g. "int") but has no parens. 
                # It is likely a variable declaration "int x = 5;". 
                # We printed it, but we do NOT enter signature mode.
                continue

            # CHECK: Global Capitalized Variables (Configuration)
            # Only preserve if we are at the top level (indent 0) or close to it
            if hide_level[-1] == -1 and self.global_var_pattern.match(stripped):
                compressed_lines.append(line)
                continue

            # 5. Handle "Implicit" Definitions (like JS methods 'getPoolSize() {')
            # We want to keep block openers, BUT we must filter out Control Flow (if, while, for).
            
            # Check if it opens a block (contains '{' or ':')
            if self.block_open_pattern.match(stripped):
                # Exclude keywords that indicate Logic/Control Flow
                # Note: We check startswith for speed, but ' in ' is safer for " } else if {"
                is_control_flow = stripped.startswith(("if", "for", "while", "switch", "catch", "else", "elif", "try", "except", "finally", "do"))
                
                if not is_control_flow:
                    compressed_lines.append(line)
                    # We do NOT enter 'signature mode' here because these are usually one-line headers 
                    # or handled by indentation, but you could optionally set in_signature=True if needed.
                
                continue

            # CHECK: Hiding Body Logic
            if hide_level[-1] != -1:
                if current_indent > hide_level[-1]:
                    if stripped.startswith("return "):
                        compressed_lines.append(line)
                    continue 
                else:
                    hide_level.pop()
        return '\n'.join(compressed_lines)
    


    def error_log_compressor(self, log_text: str) -> str:
        """
        Compresses logs for LLM ingestion by:
        1. Stitching multi-line logs (stack traces) into single units.
        2. Masking dynamic variables (UUIDs, IPs, Numbers, Hex) to create a 'Fuzzy Hash'.
        3. Deduplicating repeated structures (e.g., loops, connection retries).
        """
        lines = log_text.split('\n')
        compressed_lines = []

        # --- 1. COMPILED PATTERNS (The "Blur" Filter) ---
        # ORDER MATTERS: Specific formats first, generic numbers last.
        
        # Header Detectors (Timestamp/Level at start of line)
        # Matches: "2023-...", "Jan 01...", "[INFO]", "INFO:"

        def get_fuzzy_hash(text):
            """Creates a structural skeleton of the log for comparison."""
            # 1. Strip the Timestamp/Header from the mask (so "10:00 Error" == "10:01 Error")
            clean_text = self.header_pattern.sub('', text)
            
            # 2. Apply Masks
            for pattern, replacement in self.mask_map:
                clean_text = pattern.sub(replacement, clean_text)
                
            return clean_text.strip()

        def flush_buffer(log_entry, count, output_list):
            """Writes the log + repetition summary to output."""
            if not log_entry: return
 
            # We append the original (clean) log, not the masked one, so the LLM sees real examples.
            output_list.append(log_entry)
            
            if count > 0:
                output_list.append(f"   ... (Previous structure repeated {count} times) ...")

        
        buffer_log = ""       # Current stitched line
        buffer_mask = ""      # Skeleton of current line
        repetition_count = 0
        
        current_entry = ""    # Working variable for stitching

        for line in lines:
            stripped_right = line.rstrip() # Keep left indentation!
            if not stripped_right: continue

            # --- LOGIC: IS THIS A NEW ENTRY? ---
            # It is NEW if:
            # A) It starts with a Timestamp/Log Level (Strong Signal)
            # B) It is NOT indented (Weak Signal, assumes standard formatting)
            
            has_header = self.header_pattern.match(stripped_right)
            is_indented = len(stripped_right) - len(stripped_right.lstrip()) > 0
            
            is_new_log = has_header or not is_indented

            if is_new_log:
                # 1. PROCESS PREVIOUS COMPLETED ENTRY
                if current_entry:
                    # Calculate mask for the full multi-line block
                    current_mask = get_fuzzy_hash(current_entry)
                    
                    # Check for repetition
                    if current_mask == buffer_mask:
                        repetition_count += 1
                    else:
                        # Flush old buffer
                        if buffer_mask: # Skip initial empty state
                            flush_buffer(buffer_log, repetition_count, compressed_lines)
                        
                        # Update State
                        buffer_log = current_entry
                        buffer_mask = current_mask
                        repetition_count = 0
                
                # 2. START NEW ENTRY
                current_entry = stripped_right.strip() # Remove indentation for the main line
            
            else:
                # IT IS A CONTINUATION (Stack trace, wrapped line)
                # Stitch it!
                current_entry += " " + stripped_right.strip()

        # --- 4. FINAL FLUSH (Don't forget the last line!) ---
        if current_entry:
            current_mask = get_fuzzy_hash(current_entry)
            if current_mask == buffer_mask:
                repetition_count += 1
                flush_buffer(buffer_log, repetition_count, compressed_lines)
            else:
                if buffer_mask:
                    flush_buffer(buffer_log, repetition_count, compressed_lines)
                # Print the very last unique line
                flush_buffer(current_entry, 0, compressed_lines)

        return '\n'.join(compressed_lines)
