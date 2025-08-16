// Add this to your Chart.js configuration
options: {
    scales: {
        x: {
            ticks: {
                color: '#f8fafc' // or use var(--text-primary)
            }
            grid: {
                color: 'rgba(148, 163, 184, 0.2)'
            }
        }
        y: {
            ticks: {
                color: '#f8fafc' // or use var(--text-primary)
            }
            grid: {
                color: 'rgba(148, 163, 184, 0.2)'
            }
        }
    }
    plugins: {
        legend: {
            labels: {
                color: '#f8fafc' // or use var(--text-primary)
            }
        }
    }
}