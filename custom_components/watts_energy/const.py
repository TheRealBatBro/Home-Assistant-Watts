"""Constants for the Watts integration."""

from datetime import timedelta

DOMAIN = "watts_energy"

API_BASE_URL = "https://p.watts-energy.dk/api"
API_VERSION = "1.0"

# Azure AD B2C client-credentials flow, as documented on developer.watts-energy.dk
TOKEN_URL = (
    "https://wattsenergyassistant.b2clogin.com/wattsenergyassistant.onmicrosoft.com/"
    "B2C_1A_JITMigraion_signup_signin/oauth2/v2.0/token"
)
TOKEN_SCOPE = (
    "https://wattsenergyassistant.onmicrosoft.com/"
    "6975a1eb-15f5-41a0-b233-2568ccc7a2a5/.default"
)

CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"

# Live data (5-minute resolution) is polled on every update.
UPDATE_INTERVAL = timedelta(minutes=5)
# Locations and hourly consumption change far less often (hourly data lags 2-3 days).
SLOW_UPDATE_INTERVAL = timedelta(hours=1)

# How far back to import hourly consumption into long-term statistics on first run.
STATISTICS_BACKFILL_DAYS = 365

DEVICE_TYPE_ELECTRICITY = 4

HOUSE_TYPES = {0: "Unknown", 1: "Apartment", 2: "House", 3: "Holiday house"}
HEATING_TYPES = {
    0: "Unknown",
    1: "Wood stove",
    2: "Electrical heating",
    3: "District heating",
    4: "Gas",
    5: "Oil",
    7: "Wood pellets",
    8: "Heat pump",
    9: "Local distribution",
    10: "Geothermal",
}
