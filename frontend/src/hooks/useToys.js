import { useCallback, useRef, useState } from "react";
import { ButtplugClient, DEFAULT_INTIFACE_WS } from "@/lib/buttplug";
import { toast } from "sonner";

// Manages the connection to a local Intiface Engine (Lovense + other
// Bluetooth-compatible vibrating toys). Lives on the owner's browser, same
// as useBleHost — Bluetooth/toy control never has to touch the server.
export function useToys() {
  const clientRef = useRef(null);
  const [connected, setConnected] = useState(false);
  const [devices, setDevices] = useState([]);
  const [linked, setLinked] = useState(true); // mirror OSSM SPEED as vibration intensity

  const connect = useCallback(async (url = DEFAULT_INTIFACE_WS) => {
    const client = new ButtplugClient(url);
    client.onDevicesChanged = (list) => setDevices(list);
    client.onDisconnected = () => {
      setConnected(false);
      setDevices([]);
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
  }, []);

  const disconnect = useCallback(() => {
    clientRef.current?.disconnect();
    clientRef.current = null;
    setConnected(false);
    setDevices([]);
  }, []);

  const setDeviceIntensity = useCallback((index, value01) => {
    clientRef.current?.vibrate(index, value01).catch((e) => console.error("toy vibrate failed", e));
  }, []);

  const stopAllToys = useCallback(() => {
    clientRef.current?.stopAll().catch((e) => console.error("toy stop-all failed", e));
  }, []);

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
    handleCommand,
  };
}
