// Add error handling for missing resource
fetch('chrome-extension://pbanhockgagggenencehbnadejlgchfc/assets/userReportLinkedCandidate.json')
  .then(response => {
    if (!response.ok) {
      throw new Error(`Failed to load resource: ${response.statusText}`);
    }
    return response.json();
  })
  .then(data => {
    console.log('Resource loaded successfully:', data);
  })
  .catch(error => {
    console.error('Error loading userReportLinkedCandidate.json:', error);
    const fallbackData = { message: "Default data used as fallback" };
    console.log('Using fallback data:', fallbackData);
  });