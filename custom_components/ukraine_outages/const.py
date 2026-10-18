DOMAIN = "ukraine_outages"

CONF_REGION = "region"
CONF_OPERATOR = "operator"
CONF_GROUP = "group"
#: Key of the address for operators whose schedules come per address (Ternopil).
CONF_ADDRESS = "address"
#: The address as the user picked it ("Тернопіль, вул. Руська, 1"), for the entry title.
CONF_ADDRESS_LABEL = "address_label"
CONF_QUERY = "query"
CONF_CHOICE = "choice"

#: Minutes between schedule fetches (options flow); never below the source's own minimum.
CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 60

#: Entries of the same operator (or address) share a fetch made this many seconds ago.
SHARED_FETCH_TTL = 60

ATTRIBUTION = "Дані: оператори систем розподілу (обленерго, ДТЕК, YASNO)"
