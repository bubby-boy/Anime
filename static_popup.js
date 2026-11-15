(() => {
  const dialog = document.getElementById('screen-popup');
  if (!dialog) return;
  const title = document.getElementById('screen-title');
  const message = document.getElementById('screen-message');
  const note = document.getElementById('screen-pause-note');
  const replyForm = document.getElementById('screen-reply-form');
  const reply = document.getElementById('screen-reply');
  const send = document.getElementById('screen-send-reply');
  const replyStatus = document.getElementById('screen-reply-status');
  const errorBox = document.getElementById('screen-error');
  const dismiss = document.getElementById('screen-dismiss');
  const connection = document.getElementById('screen-connection');
  let current = null;
  let busy = false;
  let polling = false;
  let stopped = false;
  let revision = 0;

  async function request(url, data) {
    const response = await fetch(url, data === undefined ? {cache: 'no-store'} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data),
    });
    const json = await response.json();
    if (!response.ok) {
      const error = new Error(json.error || `Request failed (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return json;
  }

  function pauseVideos() {
    document.querySelectorAll('video').forEach(video => video.pause());
  }

  document.addEventListener('play', event => {
    if (current && current.pause_mode !== 'once' && event.target instanceof HTMLVideoElement) {
      event.target.pause();
    }
  }, true);

  function show(popup) {
    if (!popup) {
      const wasHeld = current && current.pause_mode !== 'once';
      current = null;
      dialog.close();
      if (wasHeld) {
        connection.textContent = 'Playback hold ended. Press Play when you are ready.';
        connection.hidden = false;
      }
      return;
    }
    const isNew = !current || current.id !== popup.id;
    current = popup;
    if (isNew) {
      title.textContent = popup.title;
      message.textContent = popup.message;
      reply.value = '';
      errorBox.textContent = '';
      replyStatus.textContent = '';
      pauseVideos();
      if (document.fullscreenElement) {
        document.exitFullscreen().catch(error => {
          errorBox.textContent = 'Exit fullscreen to read the administrator message.';
          console.error('Could not exit fullscreen for popup:', error);
        });
      }
      connection.hidden = true;
      if (!dialog.open) dialog.show();
      title.tabIndex = -1;
      title.focus();
    }
    note.textContent = {
      once: 'Playback was paused once. You can resume or dismiss this message.',
      dismiss: 'Playback is paused until you dismiss this message. You can reply first.',
      admin: 'Playback is paused until an administrator releases it. You can respond below.',
    }[popup.pause_mode];
    dismiss.disabled = busy || popup.pause_mode === 'admin';
    dismiss.textContent = popup.pause_mode === 'admin' ? 'Waiting for administrator release' : 'Dismiss message';
    reply.disabled = busy || popup.reply !== null;
    send.disabled = busy || popup.reply !== null;
    if (popup.reply !== null) {
      reply.value = popup.reply;
      replyStatus.textContent = 'Response sent. The administrator can read it in the dashboard.';
    }
    if (popup.pause_mode !== 'once') pauseVideos();
  }

  async function poll() {
    if (polling || stopped) return;
    polling = true;
    const started = revision;
    try {
      const result = await request('/api/screen-popup');
      if (started === revision) {
        if (connection.dataset.error) {
          connection.hidden = true;
          delete connection.dataset.error;
        }
        show(result.popup);
      }
    } catch (error) {
      if (error.status === 401) {
        stopped = true;
        show(null);
      }
      connection.hidden = false;
      connection.dataset.error = 'true';
      connection.textContent = error.status === 401
        ? 'Your session ended. Sign in again to receive administrator messages.'
        : 'Cannot check administrator messages. Retrying; any playback hold remains until contact is restored.';
      console.error('Popup polling failed:', error);
    } finally {
      polling = false;
      if (!stopped) window.setTimeout(poll, 2000);
    }
  }

  async function act(action, data) {
    if (busy || !current) return;
    const id = current.id;
    busy = true;
    revision++;
    show(current);
    errorBox.textContent = '';
    try {
      await request(`/api/popups/${id}/${action}`, data);
      revision++;
      if (current && current.id === id) {
        if (action === 'dismiss') show(null);
        else {
          current.reply = data.reply.trim();
          show(current);
        }
      }
    } catch (error) {
      errorBox.textContent = error.message;
    } finally {
      busy = false;
      if (current) show(current);
    }
  }

  dismiss.addEventListener('click', () => act('dismiss', {}));
  dialog.addEventListener('cancel', event => {
    event.preventDefault();
    if (current && current.pause_mode !== 'admin') act('dismiss', {});
  });
  replyForm.addEventListener('submit', event => {
    event.preventDefault();
    if (!reply.value.trim()) {
      errorBox.textContent = 'Enter a response before sending.';
      return;
    }
    act('reply', {reply: reply.value});
  });
  poll();
})();
