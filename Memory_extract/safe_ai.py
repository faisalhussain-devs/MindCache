import os
import time
import psutil
import ollama

DEFAULT_MODEL = "qwen3-fast"  
MIN_FREE_RAM_GB = 2.0           # Stop if RAM is dangerously low
MAX_CPU_LOAD_PCT = 85           # Pause if system is rendering video/gaming
MIN_BATTERY_PCT = 25            # Minimum battery to run on unplugged

# ECO MODE SETTINGS
BATCH_SIZE = 4                  # Generate 4 tokens before checking CPU
BASE_SLEEP = 0.05               # Standard cooling break (50ms)
ECO_SLEEP = 0.15                # Aggressive cooling break (150ms) if CPU is hot

class SafeAI:
    def __init__(self, model_name=DEFAULT_MODEL):
        self.model_name = model_name
        self._configure_environment()
        self._set_priority_balanced()

    def _configure_environment(self):
        """Sets internal flags to keep resource usage low."""
        # Limit Ollama to 2 threads to prevent starving the OS
        os.environ["OLLAMA_NUM_THREADS"] = "2"
        # Unload model immediately (0s) after generation to free RAM
        os.environ["OLLAMA_KEEP_ALIVE"] = "0"

    def _set_priority_balanced(self):
        """
        Sets process priority to 'BELOW_NORMAL'.
        This allows the AI to run smoothly without making the mouse lag.
        """
        try:
            p = psutil.Process(os.getpid())
            if os.name == 'nt':  # Windows
                p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            else:  # Linux / macOS (Nice value 10 is moderate low priority)
                p.nice(10)
            print(f"[SafeAI] System Priority set to Balanced .")
        except Exception as e:
            print(f"[SafeAI] Warning: Could not set priority. {e}")

    def _wait_for_safe_conditions(self):
        """
        Blocks execution if the laptop is in a critical state (Hot/Low Battery).
        """
        while True:
            issues = []
            
            # 1. Battery Check
            battery = psutil.sensors_battery()
            if battery and not battery.power_plugged:
                if battery.percent < MIN_BATTERY_PCT:
                    issues.append(f"Low Battery ({battery.percent}%)")

            # 2. CPU Safety Check
            # If the user is running a game (CPU > 85%), we wait.
            if psutil.cpu_percent(interval=0.1) > MAX_CPU_LOAD_PCT:
                issues.append("High System Load")

            # 3. RAM Check
            ram = psutil.virtual_memory()
            free_gb = ram.available / (1024 ** 3)
            if free_gb < MIN_FREE_RAM_GB:
                issues.append("Low RAM")

            if not issues:
                return  # Safe to proceed

            print(f"[SafeAI] Cooling down... Waiting for: {', '.join(issues)}")
            time.sleep(5)

    def generate(self, prompt, system_prompt=None):
        """
        Generates text using 'Dynamic Duty Cycling'.
        It pulses the CPU (Work -> Sleep -> Work) to prevent overheating.
        """
        # 1. Health Check before starting
        self._wait_for_safe_conditions()

        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        messages.append({'role': 'user', 'content': prompt})

        full_response = ""
        chunk_counter = 0

        try:
            stream = ollama.chat(
                model=self.model_name, 
                messages=messages,
                format="json", 
                stream=True,
                options={
                    "num_ctx": 4096,  # Keep context low for RAM safety
                    "temperature": 0.1
                }
            )

            for chunk in stream:
                content = chunk.get('message', {}).get('content', '')
                full_response += content
                chunk_counter += 1

                # 3. DYNAMIC COOLING LOGIC
                if chunk_counter % BATCH_SIZE == 0:
                    # Check instant CPU load. 
                    # If CPU is struggling (>70%), take a long 'Eco' break.
                    # If CPU is chill, take a short 'Base' break.
                    current_load = psutil.cpu_percent(interval=None)
                    
                    if current_load > 70:
                        time.sleep(ECO_SLEEP)  # 0.15s
                    else:
                        time.sleep(BASE_SLEEP) # 0.05s

            return full_response

        except Exception as e:
            print(f"[SafeAI] Error during generation: {e}")
            return None

    @staticmethod
    def clean_json(text):
        """Utility to strip markdown from JSON responses."""
        import re
        text = re.sub(r'```json\s*', '', text)
        text = re.sub(r'```', '', text)
        return text.strip()


if __name__ == "__main__":
    # 1. Initialize SafeAI (This sets priority and env vars)
    ai = SafeAI(model_name="qwen3-fast")

    print("Starting Safe Generation...")

    prompt = "Explain why MongoDB is flexible in one short sentence."
    response = ai.generate(prompt)
    
    if response:
        print(f"\nResult: {response}")
    else:
        print("\nGeneration failed or was stopped for safety.")