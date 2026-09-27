# Watts Energy for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/TheRealBatBro/Home-Assistant-Watts/actions/workflows/validate.yml/badge.svg)](https://github.com/TheRealBatBro/Home-Assistant-Watts/actions/workflows/validate.yml)

Unofficial Home Assistant integration for [Watts](https://watts.dk/), the Danish electricity app, using the official [Watts API](https://developer.watts-energy.dk/docs).

It gives you:

- **Live power and today's consumption** from a Watts Live reader (5-minute resolution)
- **Hourly meter data** imported into Home Assistant's long-term statistics, including a year of history, for the **Energy dashboard**
- **Electricity prices** for today and tomorrow, as a sensor ready for the Energy dashboard or price charts

> Domain is `watts_energy`, because `watts` is already taken by Home Assistant's built-in Watts Vision (thermostat) integration.

## Installation

### HACS (recommended)

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/TheRealBatBro/Home-Assistant-Watts` with category **Integration**.
3. Search for **Watts Energy**, install it and restart Home Assistant.

### Manual

Copy `custom_components/watts_energy` into your Home Assistant `config/custom_components/` folder and restart.

## Setup

1. Go to [developer.watts-energy.dk](https://developer.watts-energy.dk/keys) and log in with your Watts account.
2. Open **Keys**, click **Generate** to get a Client ID, then **Create Secret** to get a Client Secret. Copy the secret straight away — it is only shown once.
3. In Home Assistant: **Settings → Devices & services → Add integration → Watts Energy**, and paste both values.

Client secrets expire. When that happens Home Assistant will ask you to re-authenticate — create a new secret on the Keys page and paste it in.

## Entities

For each address (location) on your account:

| Entity | Description |
| --- | --- |
| Electricity price | Current price in DKK/kWh. Attributes: `today`, `tomorrow` (lists of `start`/`price`), `tomorrow_available`, `next_price`, `today_min`, `today_max`, `today_average` |

For each electricity meter (named by the last 4 digits of the meter number, and linked to its address):

| Entity | Description |
| --- | --- |
| Power | Average power over the latest 5-minute interval (W). Requires Watts Live. Max power per phase is in the attributes |
| Energy today (live) | Consumption since midnight from Watts Live (kWh, resets daily). Requires Watts Live |
| Energy last full day | Total for the most recent complete day of hourly meter data, with its `date` (usually 2–3 days ago, see below) |
| Energy this month | Month-to-date total from the hourly meter data |
| Latest hourly reading | Most recent hourly value and its `hour_start` |

Plus a long-term statistic per meter: `watts_energy:<meter id>_consumption` (or `_production` for production meters).

## Energy dashboard

Go to **Settings → Dashboards → Energy → Electricity grid → Add grid connection**. For **Grid consumption**, pick **one** of these — not both, or your usage will be counted twice:

| Option | Pick it when | Notes |
| --- | --- | --- |
| **Watts … consumption** (statistic `watts_energy:<meter id>_consumption`) | You don't have Watts Live, or you want the official meter figures | The same hourly data your grid company bills from. It arrives 2–3 days late, but it lands on the right hours, so the dashboard fills in backwards. A year of history is imported on first setup |
| **Energy today (live)** sensor | You have a Watts Live reader and want to see today's usage as it happens | Near-real-time. Any time Home Assistant is offline shows up as a gap |

For costs, select **Use an entity with current price** and choose the **Electricity price** sensor. Your Home Assistant currency must be set to DKK (**Settings → System → General**).

Production meters (solar) show up the same way and can be added under **Return to grid**.

## Update intervals and rate limit

The Watts API allows 10 requests per minute per client, and the integration stays under that automatically. If there are more requests than fit, it waits instead of failing, so the first setup of an account with many meters can take a minute.

- Live data (Watts Live): every 5 minutes
- Locations and hourly consumption, including the statistics import: every hour, with one request per meter
- Prices: only when new ones can exist — once a day for today's prices, after 13:00 for tomorrow's, and at least every 6 hours

## Data notes

Checked against the live API:

- Consumption is kWh per hour, prices are DKK/kWh, and timestamps are UTC.
- Hourly meter data from the grid (DataHub) lags 2–3 days. That's why there's no "yesterday" sensor and the Energy dashboard fills in after a delay.
- Prices come per Danish calendar day, and tomorrow's usually appear after 13:00.
- A meter with no readings at Watts shows `unknown`.

Not yet confirmed with a Watts Live reader:

- Live values (`v`) are taken as kWh per 5-minute interval, so power = `kWh × 12 × 1000` W.
- The max-power values (`MPT`, `MPL1–3`) are passed through unchanged as attributes on the Power sensor.

If something looks off, please [open an issue](https://github.com/TheRealBatBro/Home-Assistant-Watts/issues).

## Development

```bash
pip install -r requirements_test.txt
pytest
```

This project is not affiliated with Watts A/S.
