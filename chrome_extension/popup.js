document.getElementById('captureBtn').addEventListener('click', async () => {
  const status = document.getElementById('status');
  status.textContent = "Capturing...";
  
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  
  chrome.scripting.executeScript({
    target: {tabId: tab.id},
    function: () => document.body.innerText
  }, async (results) => {
    if (!results || !results[0]) {
        status.textContent = "Failed to capture text.";
        return;
    }
    const text = results[0].result;
    
    try {
      // Send to backend
      const res = await fetch('http://localhost:5000/api/stash_jd', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text}),
        credentials: 'include' 
      });
      
      const data = await res.json();
      
      if(res.ok && data.status === 'success') {
        status.textContent = "Success! Opening Dashboard...";
        setTimeout(() => {
            chrome.tabs.create({url: 'http://localhost:5000/dashboard'});
        }, 1000);
      } else {
        // If 401 Unauthorized (redirect to login) or other error
        if (res.redirected && res.url.includes('login')) {
             status.textContent = "Please login first.";
             chrome.tabs.create({url: 'http://localhost:5000/login'});
        } else {
             status.textContent = "Error: " + (data.message || "Not logged in?");
             chrome.tabs.create({url: 'http://localhost:5000/login'});
        }
      }
    } catch(e) {
      console.error(e);
      status.textContent = "Network Error. Ensure server is running at http://localhost:5000";
    }
  });
});
