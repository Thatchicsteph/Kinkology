// Minimal Buttplug protocol (v3, with v2 fallback) client.
//
// This does NOT talk to Bluetooth directly. It connects over WebSocket to a
// locally running Intiface Central / Intiface Engine process, which is the
// piece that actually holds the Bluetooth connection to Lovense and other
// supported toys (dozens of brands) and exposes a stable JSON API for
// clients like this one. Users install Intiface Central themselves
// (https://intiface.com) and press "Start Server" before connecting here —
// same spirit as the browser holding the OSSM's Web Bluetooth link.
export const DEFAULT_INTIFACE_WS = "ws://127.0.0.1:12345";

export class ButtplugClient {
  constructor(url = DEFAULT_INTIFACE_WS) {
    this.url = url;
    this.ws = null;
    this.msgId = 1;
    this.pending = new Map();
    this.devices = new Map(); // index -> { index, name, vibeCount }
    this.onDevicesChanged = null;
    this.onDisconnected = null;
  }

  connect() {
    return new Promise((resolve, reject) => {
      let ws;
      try {
        ws = new WebSocket(this.url);
      } catch (e) {
        reject(e);
        return;
      }
      this.ws = ws;
      const timeout = setTimeout(() => {
        try { ws.close(); } catch (e) {}
        reject(new Error(`Timed out reaching Intiface Engine at ${this.url}`));
      }, 5000);

      ws.onopen = async () => {
        clearTimeout(timeout);
        try {
          await this._send("RequestServerInfo", { ClientName: "OSSM Bridge", MessageVersion: 3 });
          await this._send("RequestDeviceList", {});
          resolve();
        } catch (e) {
          reject(e);
        }
      };
      ws.onerror = () => {
        clearTimeout(timeout);
        reject(new Error(`Could not reach Intiface Engine at ${this.url}. Is Intiface Central running with the server started?`));
      };
      ws.onclose = () => {
        this.devices.clear();
        this.pending.forEach(({ reject: rej }) => rej(new Error("Connection closed")));
        this.pending.clear();
        if (this.onDisconnected) this.onDisconnected();
      };
      ws.onmessage = (ev) => this._handleMessage(ev.data);
    });
  }

  disconnect() {
    if (this.ws) {
      try { this.ws.close(); } catch (e) {}
      this.ws = null;
    }
    this.devices.clear();
  }

  list() {
    return Array.from(this.devices.values());
  }

  startScanning() { return this._send("StartScanning", {}); }
  stopScanning() { return this._send("StopScanning", {}); }

  // intensity: 0..1
  async vibrate(deviceIndex, intensity) {
    const clamped = Math.min(1, Math.max(0, Number(intensity) || 0));
    const device = this.devices.get(deviceIndex);
    const count = Math.max(1, device?.vibeCount || 1);
    try {
      await this._send("ScalarCmd", {
        DeviceIndex: deviceIndex,
        Scalars: Array.from({ length: count }, (_, i) => ({ Index: i, Scalar: clamped, ActuatorType: "Vibrate" })),
      });
    } catch (e) {
      // Older Intiface Engine builds only understand the v2 VibrateCmd message.
      await this._send("VibrateCmd", {
        DeviceIndex: deviceIndex,
        Speeds: Array.from({ length: count }, (_, i) => ({ Index: i, Speed: clamped })),
      });
    }
  }

  async stopDevice(deviceIndex) {
    await this._send("StopDeviceCmd", { DeviceIndex: deviceIndex });
  }

  async stopAll() {
    await this._send("StopAllDevices", {});
  }

  _send(type, body) {
    return new Promise((resolve, reject) => {
      if (!this.ws || this.ws.readyState !== 1) {
        reject(new Error("Not connected to Intiface Engine"));
        return;
      }
      const id = this.msgId++;
      const t = setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`${type} timed out`));
        }
      }, 5000);
      this.pending.set(id, {
        resolve: (v) => { clearTimeout(t); resolve(v); },
        reject: (e) => { clearTimeout(t); reject(e); },
      });
      this.ws.send(JSON.stringify([{ [type]: { Id: id, ...body } }]));
    });
  }

  _handleMessage(raw) {
    let arr;
    try { arr = JSON.parse(raw); } catch (e) { return; }
    if (!Array.isArray(arr)) return;

    for (const msg of arr) {
      const entries = Object.entries(msg);
      if (!entries.length) continue;
      const [type, body] = entries[0];

      if (type === "DeviceAdded" || type === "DeviceList") {
        if (type === "DeviceList") this.devices.clear();
        const list = type === "DeviceList" ? (body.Devices || []) : [body];
        list.forEach((d) => this.devices.set(d.DeviceIndex, this._toDevice(d)));
        this.onDevicesChanged && this.onDevicesChanged(this.list());
      } else if (type === "DeviceRemoved") {
        this.devices.delete(body.DeviceIndex);
        this.onDevicesChanged && this.onDevicesChanged(this.list());
      }

      if (body && typeof body.Id === "number" && this.pending.has(body.Id)) {
        const { resolve, reject } = this.pending.get(body.Id);
        this.pending.delete(body.Id);
        if (type === "Error") reject(new Error(body.ErrorMessage || "Buttplug error"));
        else resolve(body);
      }
    }
  }

  _toDevice(d) {
    const scalarAttrs = d.DeviceMessages?.ScalarCmd || [];
    const vibeCount = scalarAttrs.filter((a) => (a.ActuatorType || "").toLowerCase() === "vibrate").length
      || d.DeviceMessages?.VibrateCmd?.FeatureCount
      || (d.DeviceMessages?.VibrateCmd ? 1 : 0)
      || 1;
    return { index: d.DeviceIndex, name: d.DeviceName || "Toy", vibeCount };
  }
}
