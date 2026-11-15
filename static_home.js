function readNumber(id) {
  const input = document.getElementById(id);
  if (!input.value.trim() || !Number.isFinite(Number(input.value))) {
    throw new Error('Please enter a finite number in every field.');
  }
  return Number(input.value);
}

function displayNumber(value) {
  if (!Number.isFinite(value)) {
    throw new Error('The result is outside the supported number range.');
  }
  return String(Number(value.toPrecision(12)));
}

document.getElementById('calculator').addEventListener('submit', (event) => {
  event.preventDefault();
  const output = document.getElementById('calculator-result');
  try {
    const a = readNumber('number-a');
    const b = readNumber('number-b');
    let result;
    switch (document.getElementById('operation').value) {
      case 'add': result = a + b; break;
      case 'subtract': result = a - b; break;
      case 'multiply': result = a * b; break;
      case 'divide':
        if (b === 0) throw new Error('Division by zero is undefined.');
        result = a / b;
        break;
      case 'power': result = a ** b; break;
      default: throw new Error('Choose a supported operation.');
    }
    output.textContent = `Result: ${displayNumber(result)}`;
  } catch (error) {
    output.textContent = error.message;
  }
});

document.getElementById('linear-solver').addEventListener('submit', (event) => {
  event.preventDefault();
  const output = document.getElementById('solver-result');
  try {
    const a = readNumber('coefficient-a');
    const b = readNumber('coefficient-b');
    const c = readNumber('coefficient-c');
    if (a === 0) {
      output.textContent = b === c ? 'Infinitely many solutions: every x works.' : 'No solution: the two sides cannot be equal.';
    } else {
      output.textContent = `x = ${displayNumber((c - b) / a)}`;
    }
  } catch (error) {
    output.textContent = error.message;
  }
});

const unlockForm = document.getElementById('unlock-form');
if (unlockForm) {
  unlockForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const message = document.getElementById('unlock-message');
    const button = unlockForm.querySelector('button');
    message.textContent = '';
    button.disabled = true;
    try {
      const result = await postJSON('/api/unlock', {
        password: document.getElementById('unlock-password').value,
      });
      if (!result.logged_in) {
        throw new Error('Your session expired. Sign in again, then enter the access phrase.');
      }
      window.location.assign('/hidden-library');
    } catch (error) {
      message.textContent = error.message;
      button.disabled = false;
    }
  });
}
