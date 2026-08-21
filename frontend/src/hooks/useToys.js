import { useCallback, useEffect, useRef, useState } from "react";
import { ButtplugClient, DEFAULT_INTIFACE_WS } from "@/lib/buttplug";
import { getPattern } from "@/lib/vibrationPatterns";
import { toast } from "sonner";

// Manages the connection to a local Intiface Engine (Lovense + other
// Bluetooth-compatible vibrating toys). Lives on the owner's browser, same
// as useBleHost — Bluetooth/toy control never has to touch the server.
//
// `onStatusChange({available, pattern})` is called whenever the availability
// or the currently-running pattern changes, so the owner's page can forward
// that to the backend for guest UI display.
export function useToys({ onStatusChange } = {}) {
  const clientRef = useRef(null);
  const [connected, setConnected] = useState(false);
  const [devices, setDevices] = useState([]);
  const [linked, setLinkedState] = useState(true); // mirror OSSM SPEED as vibration intensity

  const [activePattern, setActivePattern] = useState(null); // pattern id, or null
  const patternRef = useRef({ intervalId: null, startedAt: 0 });

  // Emit status changes upward whenever the guest-visible flags change.
  const available = connected && devices.length > 0;
  useEffect(() => {
    if (onStatusChange) onStatusChange({ available, pattern: activePattern });
  }, [available, activePattern, onStatusChange]);

  const stopPattern = useCallback(() => {
    if (patternRef.current.intervalId) {
      clearInterval(patternRef.current.intervalId);
      patternRef.current.intervalId = null;
    }
    setActivePattern(null);
  }, []);

  const connect = useCallback(async (url = DEFAULT_INTIFACE_WS) => {
    const client = new ButtplugClient(url);
    client.onDevicesChanged = (list) => setDevices(list);
    client.onDisconnected = () => {
      setConnected(false);
      setDevices([]);
      stopPattern();
    };
    try {
      await client.connect();
      await client.startScanning();
      clientRef.current = client;
      setConnected(true);
      toast.success("Connected to Intiface Engine");
    } catch (e) {
      toast.error(e.message || "Could not connect to Intiface Engine");
    }
  }, [stopPattern]);

  const disconnect = useCallback(() => {
    stopPattern();
    clientRef.current?.disconnect();
    clientRef.current = null;
    setConnected(false);
    setDevices([]);
  }, [stopPattern]);

  const setDeviceIntensity = useCallback((index, value01) => {
    stopPattern(); // a manual nudge always takes over from a running pattern
    clientRef.current?.vibrate(index, value01).catch((e) => console.error("toy vibrate failed", e));
  }, [stopPattern]);

  const stopAllToys = useCallback(() => {
    stopPattern();
    clientRef.current?.stopAll().catch((e) => console.error("toy stop-all failed", e));
  }, [stopPattern]);

  // Runs a named preset (see lib/vibrationPatterns.js) against every
  // connected toy until stopPattern() is called, another pattern is
  // started, a manual slider nudge happens, or the toy disconnects.
  // Only meaningful in manual mode — starting a pattern turns SPEED-link
  // off so the two engines can't fight over the same toy.
  const startPattern = useCallback((patternId) => {
    const pattern = getPattern(patternId);
    if (!pattern) return;
    const client = clientRef.current;
    if (!client) return;

    if (patternRef.current.intervalId) clearInterval(patternRef.current.intervalId);
    setLinkedState(false);

    const startedAt = Date.now();
    patternRef.current.startedAt = startedAt;
    const tick = () => {
      const t = Date.now() - startedAt;
      const intensity = Math.min(1, Math.max(0, pattern.intensityAt(t)));
      client.list().forEach((d) => client.vibrate(d.index, intensity).catch(() => {}));
    };
    tick();
    patternRef.current.intervalId = setInterval(tick, pattern.tickMs || 150);
    setActivePattern(patternId);
  }, []);

  const setLinked = useCallback((value) => {
    if (value) stopPattern(); // linking to SPEED takes back control from any running pattern
    setLinkedState(value);
  }, [stopPattern]);

  // Interpret a `toy:*` command string coming from the active guest via the
  // backend relay. Same rules as manual control — pattern & vibration are
  // mutually exclusive, and any input drops LINKED-TO-SPEED so the owner's
  // speed slider doesn't fight the guest.
  const applyRemoteCommand = useCallback((cmdStr) => {
    if (!clientRef.current) return;
    const cmd = String(cmdStr || "");
    if (cmd === "toy:stop") {
      stopPattern();
      clientRef.current.stopAll().catch(() => {});
      return;
    }
    const vib = /^toy:vibrate:(\d+)$/.exec(cmd);
    if (vib) {
      stopPattern();
      setLinkedState(false);
      const intensity = Math.min(1, Math.max(0, Number(vib[1]) / 100));
      clientRef.current.list().forEach((d) =>
        clientRef.current.vibrate(d.index, intensity).catch(() => {})
      );
      return;
    }
    const pat = /^toy:pattern:(.+)$/.exec(cmd);
    if (pat) startPattern(pat[1]);
  }, [startPattern, stopPattern]);

  // Fed every OSSM command string (owner test console, guest relay, auto
  // programs — they all funnel through one place). When linked, mirrors
  // SPEED onto every connected toy's vibration intensity.
  const handleCommand = useCallback((cmdStr) => {
    if (!linked) return;
    const client = clientRef.current;
    if (!client) return;
    const m = /^set:speed:(\d+)$/.exec(cmdStr || "");
    if (!m) return;
    const intensity = Math.min(1, Math.max(0, Number(m[1]) / 100));
    client.list().forEach((d) => client.vibrate(d.index, intensity).catch(() => {}));
  }, [linked]);

  return {
    connected,
    devices,
    linked,
    setLinked,
    connect,
    disconnect,
    setDeviceIntensity,
    stopAllToys,
    activePattern,
    startPattern,
    stopPattern,
    handleCommand,
    applyRemoteCommand,
  };
}
