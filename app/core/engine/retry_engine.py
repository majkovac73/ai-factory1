class RetryEngine:

    def __init__(self, max_retries=5):
        self.max_retries = max_retries

    def run(self, generator_fn):

        last_error = None

        for attempt in range(self.max_retries):

            result = generator_fn()

            if result is None:
                return {
                    "valid": False,
                    "error": "Generator returned None",
                    "data": None
                }

            if result.get("valid"):
                return result["data"]

            last_error = result.get("error")

            # IMPORTANT: log retry reason
            print(f"[RETRY {attempt+1}] Failed: {last_error}")

        raise Exception(f"Schema failed after retries: {last_error}")