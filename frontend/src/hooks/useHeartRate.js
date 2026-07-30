import { useCallback, useRef, useState } from "react";
import { WS_BASE } from "@/lib/api";
import { toast } from "sonner";

// Standard BLE Heart Rate Service (0x180D) + Heart Rate Measurement (0x2A37).
// Works with chest straps / optical monitors directly, and with an Apple Watch
// running a HR-broadcaster app (HeartCast, BlueHeart, etc.).
export function useHeartRate() {
  const [connected, setConnected] = useState(false);
  const [bpm, setBpm] = useState(0);
  const [deviceName, setDeviceName] = useState("");
  const deviceRef = useRef(null);
  const wsRef = useRef(null);

  const sendWs = useCallback((payload) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === 1) {
      try { ws.send(JSON.stringify(payload)); } catch (e) {}
    }
  }, []);

  const openWs = useCallback(() => {
    const token = localStorage.getItem("ossm_token");
    const ws = new WebSocket(`${WS_BASE}/api/ws/hr?token=${token}`);
    wsRef.current = ws;
    ws.onopen = () => ws.send(JSON.stringify({ type: "hr_status", connected: true }));
    ws.onclose = () => {};
  }, []);

  const parseHr = useCallback((value) => {
    // value: DataView. Flags byte bit0 => 0: uint8, 1: uint16 (little-endian).
    const flags = value.getUint8(0);
    return flags & 0x01 ? value.getUint16(1, true) : value.getUint8(1);
  }, []);

  const onDisconnected = useCallback(() => {
    setConnected(false);
    setBpm(0);
    sendWs({ type: "hr_status", connected: false });
    if (wsRef.current) { try { wsRef.current.close(); } catch (e) {} wsRef.current = null; }
    toast.error("Heart rate monitor disconnected");
  }, [sendWs]);

  const connect = useCallback(async () => {
    if (!navigator.bluetooth) {
      toast.error("Web Bluetooth not supported. Use Chrome, Edge, or Opera.");
      return;
    }
    try {
      const device = await navigator.bluetooth.requestDevice({
        filters: [{ services: ["heart_rate"] }],
        optionalServices: ["heart_rate"],
      });
      deviceRef.current = device;
      device.addEventListener("gattserverdisconnected", onDisconnected);
      const server = await device.gatt.connect();
      const service = await server.getPrimaryService("heart_rate");
      const char = await service.getCharacteristic("heart_rate_measurement");
      await char.startNotifications();
      openWs();
      char.addEventListener("characteristicvaluechanged", (e) => {
        const val = parseHr(e.target.value);
        setBpm(val);
        sendWs({ type: "hr", bpm: val });
      });
      setDeviceName(device.name || "Heart Rate");
      setConnected(true);
      toast.success(`Heart rate monitor connected${device.name ? ` — ${device.name}` : ""}`);
    } catch (e) {
      if (e && e.name !== "NotFoundError") toast.error(`HR connection failed: ${e.message}`);
    }
  }, [onDisconnected, openWs, parseHr, sendWs]);

  const disconnect = useCallback(() => {
    if (deviceRef.current?.gatt?.connected) deviceRef.current.gatt.disconnect();
    onDisconnected();
  }, [onDisconnected]);

  return { connected, bpm, deviceName, connect, disconnect };
}
