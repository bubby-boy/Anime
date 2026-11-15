async function postJSON(url, data) {
  const response = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data),
  });
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    throw new Error(`Unexpected server response (${response.status}). Please try again.`);
  }
  const result = await response.json();
  if (!response.ok) {
    throw new Error(result.error || (response.status === 401 ? 'Incorrect access phrase or expired session.' : `Request failed (${response.status}).`));
  }
  return result;
}

const logoutButton = document.getElementById('logout');
if (logoutButton) {
  logoutButton.addEventListener('click', async () => {
    logoutButton.disabled = true;
    try {
      await postJSON('/api/logout', {});
      window.location.assign('/');
    } catch (error) {
      document.getElementById('site-message').textContent = error.message;
      logoutButton.disabled = false;
    }
  });
}
