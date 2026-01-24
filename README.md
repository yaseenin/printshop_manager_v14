# Print Shop Manager v14

A comprehensive management tool for print shops, featuring printer monitoring via SNMP, analytics, reporting, and consumable tracking.

## Features

- **Dashboard**: Real-time status of all printers (Online/Offline).
- **Machines**: Manage printer inventory, view counters, and access Web UIs.
- **Analytics**: Track usage trends with heatmaps, daily breakdowns, and peak hour analysis.
- **Reports**: Generate usage reports and export to CSV/Excel.
- **Consumables**: Track toner and drum levels.
- **API Server**: Built-in Flask API for external integrations.

## Installation

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/yourusername/printshop-manager.git
    cd printshop-manager
    ```

2.  **Create a virtual environment** (recommended):
    ```bash
    python -m venv venv
    # Windows
    venv\Scripts\activate
    # Linux/Mac
    source venv/bin/activate
    ```

3.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

## Usage

Run the main application:

```bash
python main.py
```

The application will start the UI and a background API server (default port 5000).