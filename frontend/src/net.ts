export function wsUrl(path: string): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${path}`;
}

/** WebSocket JSON z automatycznym ponownym łączeniem. */
export function connectJson(path: string, onMsg: (m: any) => void, onState: (up: boolean) => void): void {
  let delay = 500;
  const open = () => {
    const ws = new WebSocket(wsUrl(path));
    ws.onopen = () => { delay = 500; onState(true); };
    ws.onmessage = (e) => { try { onMsg(JSON.parse(e.data as string)); } catch { /* pomiń */ } };
    ws.onclose = () => { onState(false); setTimeout(open, delay); delay = Math.min(delay * 1.6, 4000); };
    ws.onerror = () => ws.close();
  };
  open();
}

export async function post(path: string, body?: unknown): Promise<any> {
  const r = await fetch(path, {
    method: "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return r.json();
}
