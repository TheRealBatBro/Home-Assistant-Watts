# Watts Energy for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/TheRealBatBro/Home-Assistant-Watts/actions/workflows/validate.yml/badge.svg)](https://github.com/TheRealBatBro/Home-Assistant-Watts/actions/workflows/validate.yml)

Unofficial Home Assistant integration for [Watts](https://watts.dk/), the Danish electricity app, using the official [Watts API](https://developer.watts-energy.dk/docs).

It gives you:

- **Live power and today's consumption** from a Watts Live reader (5-minute resolution)
- **Hourly meter data** imported into Home Assistant's long-term statistics, including 90 days of history, for the **Energy dashboard**
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

For each electricity meter:

| Entity | Description |
| --- | --- |
| Power | Average power over the latest 5-minute interval (W). Requires Watts Live. Max power per phase is in the attributes |
| Energy today (live) | Consumption since midnight from Watts Live (kWh, resets daily). Requires Watts Live |
| Energy yesterday | Yesterday's total from the hourly meter data |
| Energy this month | Month-to-date total from the hourly meter data |
| Latest hourly reading | Most recent hourly value and its `hour_start` |

Plus a long-term statistic per meter: `watts_energy:<meter id>_consumption` (or `_production` for production meters).

## Energy dashboard

Go to **Settings → Dashboards → Energy → Electricity grid → Add grid connection**. For **Grid consumption**, pick **one** of these — not both, or your usage will be counted twice:

| Option | Pick it when | Notes |
| --- | --- | --- |
| **Watts … consumption** (statistic `watts_energy:<meter id>_consumption`) | You don't have Watts Live, or you want the official meter figures | The same hourly data your grid company bills from. It usually arrives 1–2 days late, but it lands on the right hours, so the dashboard fills in backwards. 90 days of history are imported on first setup |
| **Energy today (live)** sensor | You have a Watts Live reader and want to see today's usage as it happens | Near-real-time. Any time Home Assistant is offline shows up as a gap |

For costs, select **Use an entity with current price** and choose the **Electricity price** sensor. Your Home Assistant currency must be set to DKK (**Settings → System → General**).

Production meters (solar) show up the same way and can be added under **Return to grid**.

## Update intervals

- Live data: every 5 minutes
- Hourly consumption, statistics import and prices: every hour

## Notes and assumptions

The Watts API docs don't state units for everything. This integration assumes:

- Consumption values (`v`) are kWh per interval. Power is worked out as `kWh per 5 min × 12 × 1000` W.
- Prices (`p`) are DKK/kWh.
- Timestamps without a time zone are UTC.
- The max-power values (`MPT`, `MPL1–3`) are passed through unchanged as attributes on the Power sensor, with no unit conversion.

If your numbers look off by a factor of 1000 or by an hour, please [open an issue](https://github.com/TheRealBatBro/Home-Assistant-Watts/issues) with an example.

## Development

```bash
pip install -r requirements_test.txt
pytest
```

This project is not affiliated with Watts A/S.
