# ==========================
# ELIAN — event / proactive diagnostics (Phase 6)
# ==========================

from __future__ import annotations


def run_event_diagnostics(*, test_event: str = "") -> int:
    print("=== ELIAN EVENT DIAGNOSTICS ===")
    print()

    from core.clock import SystemClock
    from core.event_bus import EventBus
    from core.events import BATTERY_LOW, Event, Priority
    from core.proactive import ProactiveManager
    from core.system_state import SystemStateStore
    from watchers.system import SystemWatcher
    from watchers.system_provider import PsutilSystemProvider, FakeSystemProvider
    from watchers.home_assistant import HomeAssistantWatcher, FakeHAHealth

    clock = SystemClock()
    bus = EventBus(clock=clock)
    store = SystemStateStore()
    spoken = []

    proactive = ProactiveManager(
        clock=clock,
        speak_fn=lambda m: spoken.append(m),
        busy_check=lambda: False,
    )
    bus.subscribe(proactive.handle_event)

    provider = PsutilSystemProvider()
    try:
        snap = provider.snapshot()
    except Exception as exc:
        print(f"System provider error: {exc}")
        snap = FakeSystemProvider().snapshot()

    print("EventBus: READY")
    print("ProactiveManager: READY")
    print()
    print("System watcher snapshot:")
    print(f"  battery: {snap.battery_percent} (has={snap.has_battery}, plugged={snap.battery_plugged})")
    print(f"  cpu: {snap.cpu_percent:.1f}%")
    print(f"  ram: {snap.ram_percent:.1f}%")
    print(f"  disk_free_gb: {snap.disk_free_gb}")
    print(f"  network: {'online' if snap.network_online else 'offline'}")
    print()

    ha = HomeAssistantWatcher(bus, health_fn=FakeHAHealth(True), clock=clock, state_store=store)
    ha.start()
    ha.poll_once()
    print(f"HA watcher: available={store.state.ha_available}")
    print()

    if test_event == "battery_low":
        print("Developer test event: battery_low (no real voice unless wired)")
        bus.publish(
            Event(
                type=BATTERY_LOW,
                source="diagnostics",
                data={"percent": 5, "threshold": 5},
                priority=Priority.HIGH,
                timestamp=clock.time(),
                dedup_key="diagnostics:battery_5",
            )
        )
        print(f"Spoken (capture): {spoken}")

    print("=== END EVENT DIAGNOSTICS ===")
    return 0
