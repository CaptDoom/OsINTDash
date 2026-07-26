import { WebSocketServer } from 'ws';

let wss = null;
const clients = new Map();

export const initWebSocket = (server) => {
  wss = new WebSocketServer({ noServer: true });

  wss.on('connection', (ws) => {
    const clientId = `client_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`;
    clients.set(clientId, ws);

    console.log(`[WS] Client ${clientId} connected. Total: ${clients.size}`);

    ws.on('message', (message) => {
      try {
        const data = JSON.parse(message);
        if (data.type === 'ping') {
          ws.send(JSON.stringify({ type: 'pong' }));
        }
      } catch (err) {
        // ignore
      }
    });

    ws.on('close', () => {
      clients.delete(clientId);
      console.log(`[WS] Client ${clientId} disconnected. Total: ${clients.size}`);
    });
  });

  // Listen to upgrade requests
  server.on('upgrade', (request, socket, head) => {
    // Only upgrade if it's matching ws or standard path
    if (wss) {
      wss.handleUpgrade(request, socket, head, (ws) => {
        wss.emit('connection', ws, request);
      });
    }
  });

  console.log('[WS] WebSocket server attached to HTTP upgrade cycle.');
};

export const broadcastJobUpdate = (jobId, status, progress, result = null, error = null) => {
  const payload = JSON.stringify({
    type: 'job_update',
    jobId,
    status,
    progress,
    result,
    error
  });

  for (const client of clients.values()) {
    if (client.readyState === 1) { // OPEN
      try {
        client.send(payload);
      } catch (err) {
        // fail silently
      }
    }
  }
};
