import importlib.util

# Home Assistant tests need their own environment (see README); skip them elsewhere.
collect_ignore = [] if importlib.util.find_spec("homeassistant") else ["ha"]
