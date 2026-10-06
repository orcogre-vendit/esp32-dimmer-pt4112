#!/usr/bin/env python3
"""PT4112 dimmer control GUI (tkinter).

Talks to the ESP32-C3 firmware over either BLE UART (Nordic UART Service)
or USB serial, using the same line protocol:

    SET <ch> <level> [fade_ms]   ALL <level> [fade_ms]
    GET <ch>   STATE   INFO   PING

Run:  python dimmer_gui.py
Deps: bleak, pyserial  (pip install -r requirements.txt)
"""

import asyncio
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import serial
import serial.tools.list_ports
from bleak import BleakClient, BleakScanner

NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # write
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # notify

SLIDER_THROTTLE_S = 0.05


# ------------------------------------------------------------------ Transports
class Transport:
    """Base: send lines, receive lines via on_line(str), on_close(reason)."""

    def __init__(self, on_line, on_close):
        self.on_line = on_line
        self.on_close = on_close

    def send(self, line: str):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError


class SerialTransport(Transport):
    def __init__(self, port, baud, on_line, on_close):
        super().__init__(on_line, on_close)
        self.ser = serial.Serial(port, baud, timeout=0.1)
        self._alive = True
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _reader(self):
        buf = b""
        while self._alive:
            try:
                data = self.ser.read(256)
            except serial.SerialException as e:
                self._alive = False
                self.on_close(f"serial error: {e}")
                return
            if not data:
                continue
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self.on_line(line.decode(errors="replace").strip())

    def send(self, line: str):
        self.ser.write((line + "\n").encode())

    def close(self):
        self._alive = False
        try:
            self.ser.close()
        except Exception:
            pass


class BleTransport(Transport):
    """Runs its own asyncio loop in a thread; send() is thread-safe."""

    def __init__(self, address, on_line, on_close, on_ready):
        super().__init__(on_line, on_close)
        self.address = address
        self.on_ready = on_ready
        self.loop = asyncio.new_event_loop()
        self.client = None
        self._rxbuf = b""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    async def _main(self):
        try:
            self.client = BleakClient(self.address, disconnected_callback=self._disconnected)
            await self.client.connect()
            await self.client.start_notify(NUS_TX, self._notify)
            self.on_ready()
            self._stop = asyncio.Event()
            await self._stop.wait()
        except Exception as e:
            self.on_close(f"BLE error: {e}")
            return
        try:
            await self.client.disconnect()
        except Exception:
            pass

    def _disconnected(self, _client):
        self.on_close("BLE disconnected")

    def _notify(self, _char, data: bytearray):
        self._rxbuf += bytes(data)
        while b"\n" in self._rxbuf:
            line, self._rxbuf = self._rxbuf.split(b"\n", 1)
            self.on_line(line.decode(errors="replace").strip())

    async def _write(self, payload: bytes):
        # NUS RX write; response=False is fine for WRITE_NR characteristic
        await self.client.write_gatt_char(NUS_RX, payload, response=False)

    def send(self, line: str):
        if self.client and self.client.is_connected:
            asyncio.run_coroutine_threadsafe(self._write((line + "\n").encode()), self.loop)

    def close(self):
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(lambda: self._stop.set() if hasattr(self, "_stop") else None)


def ble_scan(timeout=4.0):
    """Blocking scan; returns list of (name, address) with NUS service or matching name."""

    async def _scan():
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
        out = []
        for dev, adv in found.values():
            uuids = [u.lower() for u in (adv.service_uuids or [])]
            name = dev.name or adv.local_name or ""
            if NUS_SERVICE in uuids or "dimmer" in name.lower():
                out.append((name or "(no name)", dev.address))
        return out

    return asyncio.run(_scan())


# ------------------------------------------------------------------ GUI
class DimmerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PT4112 Dimmer")
        self.geometry("520x560")
        self.minsize(440, 420)

        self.transport = None
        self.rx_queue = queue.Queue()
        self.channels = []          # list of dict(var, scale, label, last_sent, pending)
        self.num_channels = 0
        self._build_ui()
        self.after(50, self._poll_rx)
        self.protocol("WM_DELETE_WINDOW", self._on_quit)

    # ---------- UI construction
    def _build_ui(self):
        pad = dict(padx=6, pady=4)

        conn = ttk.LabelFrame(self, text="Connection")
        conn.pack(fill="x", **pad)

        self.mode = tk.StringVar(value="ble")
        ttk.Radiobutton(conn, text="BLE", variable=self.mode, value="ble", command=self._mode_changed).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(conn, text="USB Serial", variable=self.mode, value="serial", command=self._mode_changed).grid(row=0, column=1, sticky="w")

        self.target_var = tk.StringVar()
        self.target_combo = ttk.Combobox(conn, textvariable=self.target_var, state="readonly", width=34)
        self.target_combo.grid(row=1, column=0, columnspan=2, sticky="we", padx=4)
        self.scan_btn = ttk.Button(conn, text="Scan", command=self._scan)
        self.scan_btn.grid(row=1, column=2, padx=2)
        self.conn_btn = ttk.Button(conn, text="Connect", command=self._toggle_connect)
        self.conn_btn.grid(row=1, column=3, padx=2)
        conn.columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(value="Disconnected")
        ttk.Label(conn, textvariable=self.status_var, foreground="gray").grid(row=2, column=0, columnspan=4, sticky="w", padx=4)

        self.ch_frame = ttk.LabelFrame(self, text="Channels")
        self.ch_frame.pack(fill="x", **pad)
        self._build_channels(0)

        allf = ttk.Frame(self)
        allf.pack(fill="x", **pad)
        ttk.Button(allf, text="All ON", command=lambda: self._send("ALL 255")).pack(side="left", padx=2)
        ttk.Button(allf, text="All OFF", command=lambda: self._send("ALL 0")).pack(side="left", padx=2)
        ttk.Button(allf, text="STATE", command=lambda: self._send("STATE")).pack(side="left", padx=2)
        ttk.Button(allf, text="INFO", command=lambda: self._send("INFO")).pack(side="left", padx=2)
        ttk.Button(allf, text="PING", command=lambda: self._send("PING")).pack(side="left", padx=2)

        rawf = ttk.Frame(self)
        rawf.pack(fill="x", **pad)
        self.raw_var = tk.StringVar()
        ent = ttk.Entry(rawf, textvariable=self.raw_var)
        ent.pack(side="left", fill="x", expand=True)
        ent.bind("<Return>", lambda e: self._send_raw())
        ttk.Button(rawf, text="Send", command=self._send_raw).pack(side="left", padx=4)

        logf = ttk.LabelFrame(self, text="Log")
        logf.pack(fill="both", expand=True, **pad)
        mono = "Menlo" if sys.platform == "darwin" else ("Consolas" if sys.platform == "win32" else "DejaVu Sans Mono")
        self.log = tk.Text(logf, height=10, state="disabled", font=(mono, 11))
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self._mode_changed()

    def _build_channels(self, n):
        for w in self.ch_frame.winfo_children():
            w.destroy()
        self.channels = []
        self.num_channels = n
        if n == 0:
            ttk.Label(self.ch_frame, text="(connect to load channels)", foreground="gray").pack(padx=6, pady=6)
            return
        for i in range(n):
            row = ttk.Frame(self.ch_frame)
            row.pack(fill="x", padx=4, pady=2)
            ttk.Label(row, text=f"CH {i}", width=6).pack(side="left")
            var = tk.IntVar(value=0)
            val_lbl = ttk.Label(row, text="0", width=4, anchor="e")
            scale = ttk.Scale(row, from_=0, to=255, orient="horizontal", variable=var,
                              command=lambda v, idx=i: self._slider_moved(idx, v))
            scale.pack(side="left", fill="x", expand=True, padx=4)
            scale.bind("<ButtonRelease-1>", lambda e, idx=i: self._slider_released(idx))
            val_lbl.pack(side="left")
            ttk.Button(row, text="ON", width=4, command=lambda idx=i: self._set_level(idx, 255)).pack(side="left", padx=2)
            ttk.Button(row, text="OFF", width=4, command=lambda idx=i: self._set_level(idx, 0)).pack(side="left")
            self.channels.append(dict(var=var, scale=scale, label=val_lbl, last_sent=0.0, pending=None, suppress=False))

    # ---------- connection handling
    def _mode_changed(self):
        self.target_combo.set("")
        self.target_combo["values"] = []
        if self.mode.get() == "serial":
            self.scan_btn.config(text="Refresh")
            self._refresh_ports()
        else:
            self.scan_btn.config(text="Scan")

    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()
                 if "Bluetooth" not in p.device and "debug" not in p.device]
        self.target_combo["values"] = ports
        if ports:
            self.target_combo.current(0)

    def _scan(self):
        if self.mode.get() == "serial":
            self._refresh_ports()
            return
        self.scan_btn.config(state="disabled")
        self._set_status("Scanning BLE...")

        def work():
            try:
                devs = ble_scan()
            except Exception as e:
                devs = e
            self.after(0, lambda: self._scan_done(devs))

        threading.Thread(target=work, daemon=True).start()

    def _scan_done(self, devs):
        self.scan_btn.config(state="normal")
        if isinstance(devs, Exception):
            self._set_status(f"Scan failed: {devs}")
            return
        self._ble_devices = devs
        self.target_combo["values"] = [f"{n}  [{a}]" for n, a in devs]
        if devs:
            self.target_combo.current(0)
            self._set_status(f"Found {len(devs)} device(s)")
        else:
            self._set_status("No dimmer found")

    def _toggle_connect(self):
        if self.transport:
            self._disconnect("Disconnected")
            return
        target = self.target_var.get()
        if not target:
            messagebox.showwarning("No target", "Scan / select a device first.")
            return
        try:
            if self.mode.get() == "serial":
                self.transport = SerialTransport(target, 115200, self._rx_line, self._rx_closed)
                self._connected()
            else:
                idx = self.target_combo.current()
                addr = self._ble_devices[idx][1]
                self._set_status(f"Connecting {addr}...")
                self.conn_btn.config(state="disabled")
                self.transport = BleTransport(addr, self._rx_line, self._rx_closed,
                                              lambda: self.after(0, self._connected))
        except Exception as e:
            self.transport = None
            messagebox.showerror("Connect failed", str(e))

    def _connected(self):
        self.conn_btn.config(text="Disconnect", state="normal")
        self._set_status(f"Connected ({self.mode.get()})")
        self._log("--- connected")
        self._send("INFO")
        self._send("STATE")

    def _rx_closed(self, reason):
        self.after(0, lambda: self._disconnect(reason))

    def _disconnect(self, reason):
        if self.transport:
            t, self.transport = self.transport, None
            t.close()
        self.conn_btn.config(text="Connect", state="normal")
        self._set_status(reason)
        self._log(f"--- {reason}")
        self._build_channels(0)

    # ---------- RX
    def _rx_line(self, line):
        self.rx_queue.put(line)

    def _poll_rx(self):
        try:
            while True:
                line = self.rx_queue.get_nowait()
                self._handle_line(line)
        except queue.Empty:
            pass
        # flush throttled slider values
        now = time.time()
        for i, c in enumerate(self.channels):
            if c["pending"] is not None and now - c["last_sent"] >= SLIDER_THROTTLE_S:
                self._send(f"SET {i} {c['pending']} 50")
                c["last_sent"] = now
                c["pending"] = None
        self.after(30, self._poll_rx)

    def _handle_line(self, line):
        self._log(f"< {line}")
        parts = line.split()
        if not parts:
            return
        if parts[0] == "INFO":
            kv = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
            n = int(kv.get("ch", 0))
            if n != self.num_channels:
                self._build_channels(n)
            self.title(f"PT4112 Dimmer  fw {kv.get('fw', '?')}  {n} ch")
        elif parts[0] == "STATE" and len(parts) > 1:
            levels = [int(x) for x in parts[1].split(",") if x.isdigit()]
            if len(levels) != self.num_channels:
                self._build_channels(len(levels))
            for i, lv in enumerate(levels):
                c = self.channels[i]
                if c["pending"] is None:   # don't fight the user's drag
                    c["suppress"] = True
                    c["var"].set(lv)
                    c["label"].config(text=str(lv))
                    c["suppress"] = False
        elif parts[0] == "CH" and len(parts) == 3:
            i, lv = int(parts[1]), int(parts[2])
            if i < self.num_channels:
                self.channels[i]["var"].set(lv)
                self.channels[i]["label"].config(text=str(lv))

    # ---------- TX
    def _send(self, line):
        if not self.transport:
            return
        self._log(f"> {line}")
        try:
            self.transport.send(line)
        except Exception as e:
            self._disconnect(f"send failed: {e}")

    def _send_raw(self):
        line = self.raw_var.get().strip()
        if line:
            self._send(line)
            self.raw_var.set("")

    def _set_level(self, idx, level):
        c = self.channels[idx]
        c["pending"] = None
        c["suppress"] = True
        c["var"].set(level)
        c["label"].config(text=str(level))
        c["suppress"] = False
        self._send(f"SET {idx} {level}")

    def _slider_moved(self, idx, value):
        c = self.channels[idx]
        lv = int(float(value))
        c["label"].config(text=str(lv))
        if c["suppress"]:
            return
        c["pending"] = lv     # flushed by _poll_rx with throttle

    def _slider_released(self, idx):
        c = self.channels[idx]
        lv = int(c["var"].get())
        c["pending"] = None
        self._send(f"SET {idx} {lv} 50")
        c["last_sent"] = time.time()

    # ---------- misc
    def _set_status(self, text):
        self.status_var.set(text)

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        # keep log bounded
        if int(self.log.index("end-1c").split(".")[0]) > 500:
            self.log.delete("1.0", "100.0")
        self.log.configure(state="disabled")

    def _on_quit(self):
        if self.transport:
            self.transport.close()
        self.destroy()


if __name__ == "__main__":
    DimmerApp().mainloop()
