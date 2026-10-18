from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_ADDRESS, CONF_ADDRESS_LABEL
from .coordinator import OutagesConfigEntry

TO_REDACT = {CONF_ADDRESS, CONF_ADDRESS_LABEL, "title", "unique_id"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: OutagesConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data({"title": entry.title, "unique_id": entry.unique_id, **entry.data}, TO_REDACT),
        "options": dict(entry.options),
        "operator": coordinator.operator.id,
        "group": coordinator.group,
        "last_update_success": coordinator.last_update_success,
        "last_source": coordinator.last_source,
        "update_interval_minutes": coordinator.update_interval.total_seconds() / 60 if coordinator.update_interval else None,
        "days": {
            day.isoformat(): {
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                "groups": len(s.groups),
                "unknown": len(s.unknown),
                "outages": [[o.start.isoformat(), o.end.isoformat(), o.possible] for o in s.outages(coordinator.group)],
            }
            for day, s in (coordinator.data or {}).items()
        },
    }
