// Handles API calls to backend

document.addEventListener('DOMContentLoaded', function() {
    const runBtn = document.getElementById('runBtn');
    const symbolInput = document.getElementById('symbolInput');
    const outputDiv = document.getElementById('output');

    runBtn.addEventListener('click', function() {
        const symbol = symbolInput.value.trim().toUpperCase();
        if (!symbol) {
            outputDiv.innerHTML = '<p>Please enter a stock symbol.</p>';
            return;
        }

        outputDiv.innerHTML = '<p>Running pipeline...</p>';

        fetch(`http://localhost:5001/analyze?symbol=${encodeURIComponent(symbol)}`)
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    outputDiv.innerHTML = `<p>Error: ${data.error}</p>`;
                    return;
                }
                outputDiv.innerHTML = `
                    <p><strong>Symbol:</strong> ${data.symbol}</p>
                    <p><strong>Decision:</strong> ${data.decision}</p>
                    <p><strong>Confidence:</strong> ${(data.confidence * 100).toFixed(1)}%</p>
                    <p><strong>Reason:</strong> ${data.reason}</p>
                    <p><strong>Pattern:</strong> ${data.pattern_detected.pattern} (${data.pattern_detected.direction})</p>
                    <p><strong>Entry:</strong> ${data.risk.entry ?? 'N/A'}</p>
                    <p><strong>Stop Loss:</strong> ${data.risk.stop_loss ?? 'N/A'}</p>
                    <p><strong>Target:</strong> ${data.risk.target ?? 'N/A'}</p>
                    <p><strong>R:R Ratio:</strong> ${data.risk.rr_ratio ?? 'N/A'}</p>
                `;
            })
            .catch(error => {
                outputDiv.innerHTML = `<p>Error: ${error}</p>`;
            });
    });
});