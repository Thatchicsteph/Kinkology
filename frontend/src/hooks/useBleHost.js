import { useCallback, useRef, useState } from "react";
import { WS_BASE, API } from "@/lib/api";
import { OSSM } from "@/lib/ossm";
import { toast } from "sonner";

export function useBleHost() {
  const [connected, setConnected] = useState(false);
  const [deviceName, setDeviceName] = useState("");
  const [wsConnected, setWsConnected] = useState(false);
  const cmdCharRef = useRef(null);
  const deviceRef = useRef(null);
  const wsRef = useRef(null);
  const lastCmdRef = useRef("");

  const writeCommand = useCallback(async (command) => {
    const char = cmdCharRef.current;
    if (!char) return;
    try {
      const data = new TextEncoder().encode(command);
      if (char.writeValueWithoutResponse) await char.writeValueWithoutResponse(data);
      else await char.writeValue(data);
      lastCmdRef.current = command;
    } catch (e) {
      console.error("BLE write failed", command, e);
    }
  }, []);

  const openHostWs = useCallback(() => {
    const token = localStorage.getItem("ossm_token");
    const ws = new WebSocket(`${WS_BASE}/api/ws/host?token=${token}`);
    wsRef.current = ws;
    ws.onopen = () => setWsConnected(true);
    ws.onclose = () => setWsConnected(false);
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "command" && msg.cmd) writeCommand(msg.cmd);
      } catch (e) {}
    };
  }, [writeCommand]);

  const onDisconnected = useCallback(() => {
    setConnected(false);
    cmdCharRef.current = null;
    if (wsRef.current) {
      try { wsRef.current.send(JSON.stringify({ type: "ble_status", connected: false })); } catch (e) {}
      wsRef.current.close();
      wsRef.current = null;
    }
    toast.error("Device disconnected");
  }, []);

  const connect = useCallback(async () => {
    if (!navigator.bluetooth) {
      toast.error("Web Bluetooth not supported. Use Chrome, Edge, or Opera.");
      return;
    }
    try {
      const device = await navigator.bluetooth.requestDevice({
        filters: [{ services: [OSSM.SERVICE_UUID] }],
        optionalServices: [OSSM.SERVICE_UUID],
      });
      deviceRef.current = device;
      device.addEventListener("gattserverdisconnected", onDisconnected);
      const server = await device.gatt.connect();
      const service = await server.getPrimaryService(OSSM.SERVICE_UUID);
      cmdCharRef.current = await service.getCharacteristic(OSSM.COMMAND_UUID);

      // Subscribe to device state notifications (best effort)
      try {
        const stateChar = await service.getCharacteristic(OSSM.STATE_UUID);
        await stateChar.startNotifications();
        stateChar.addEventListener("characteristicvaluechanged", (e) => {
          const val = new TextDecoder().decode(e.target.value);
          if (wsRef.current && wsRef.current.readyState === 1) {
            wsRef.current.send(JSON.stringify({ type: "device_state", state: val }));
          }
        });
      } catch (e) { /* state char optional */ }

      setDeviceName(device.name || "OSSM");
      setConnected(true);
      openHostWs();
      toast.success(`Connected to ${device.name || "OSSM"}`);
    } catch (e) {
      if (e && e.name !== "NotFoundError") toast.error(`Connection failed: ${e.message}`);
    }
  }, [onDisconnected, openHostWs]);

  const disconnect = useCallback(() => {
    if (deviceRef.current?.gatt?.connected) deviceRef.current.gatt.disconnect();
    onDisconnected();
  }, [onDisconnected]);

  return { connected, wsConnected, deviceName, connect, disconnect, writeCommand };
}
