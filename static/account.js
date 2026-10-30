/* Account forms keep their content on a network or gateway failure. */
(() => {
  for (const form of document.querySelectorAll('form[data-account-form]')) {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const button = form.querySelector('button[type="submit"], button');
      if (button.disabled) return;
      const original = button.textContent;
      button.disabled = true;
      button.textContent = 'Please wait…';
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 20000);
      try {
        const response = await fetch(form.action || location.href, {
          method: 'POST', body: new FormData(form), credentials: 'same-origin',
          signal: controller.signal, redirect: 'follow'
        });
        if (response.redirected && response.ok) {
          location.assign(response.url);
          return;
        }
        const html = await response.text();
        const parsed = new DOMParser().parseFromString(html, 'text/html');
        const notice = parsed.querySelector('.notice');
        let message = notice?.textContent?.trim();
        if (!message) message = response.status === 429
          ? 'Too many attempts. Please wait 15 minutes before trying again.'
          : 'We couldn’t complete this request. Please try again shortly. If you were creating an account, you can also try signing in with the same details.';
        showError(form, message);
      } catch (error) {
        showError(form, 'The connection took too long. Please try again shortly. If you were creating an account, you can also try signing in with the same details.');
      } finally {
        clearTimeout(timer);
        button.disabled = false;
        button.textContent = original;
      }
    });
  }
  function showError(form, message) {
    let notice = form.parentElement.querySelector('[data-account-error]');
    if (!notice) {
      notice = document.createElement('p');
      notice.className = 'notice';
      notice.setAttribute('role', 'alert');
      notice.dataset.accountError = '';
      form.before(notice);
    }
    notice.textContent = message;
  }
  const status = document.getElementById('delivery-status');
  if (!status || status.dataset.state !== 'sending') return;
  const started = Date.now();
  async function poll() {
    try {
      const response = await fetch('/api/account-verification', {
        credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(8000)
      });
      if (!response.ok) throw new Error('status unavailable');
      const data = await response.json();
      if (data.state === 'sent') {
        status.textContent = 'Your code has been sent. Check your inbox and spam folder.';
        return;
      }
      if (data.state === 'failed' || data.state === 'expired') {
        status.textContent = data.state === 'expired'
          ? 'This verification request has expired. Please sign in again.'
          : 'We couldn’t confirm that your code was sent. If it arrives, enter it below. Otherwise, request a new code.';
        return;
      }
    } catch (error) { /* A short network interruption may recover on the next check. */ }
    if (Date.now() - started >= 45000) {
      status.textContent = 'We couldn’t confirm delivery. If your code arrives, enter it below. Otherwise, request a new code.';
      return;
    }
    setTimeout(poll, 2000);
  }
  poll();
})();
