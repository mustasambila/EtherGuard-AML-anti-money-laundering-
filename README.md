EtherGuard Project - README

========================================
1. Installing Dependencies
========================================
- Ensure Python 3.8 or higher is installed on your system.
- Open a terminal/command prompt.
- Navigate to the 'fyp' folder on the CD.
- Install required Python packages using:
  pip install -r requirements.txt

========================================
2. Running the Application
========================================
- Option 1: Using the Python source code
  python app.py

- Option 2: Using the executable binary (Windows only)
  Navigate to the 'dist' folder and double-click 'app.exe'

- The web application will start on http://localhost:5000

========================================
3. Using the System
========================================
- Open your web browser and go to http://localhost:5000
- Register a new account or log in.
- Complete KYC submission for full access.
- Track Ethereum wallets, manage your watchlist, and export PDF reports.
- Admins can review KYC requests and monitor suspicious activity.
- For detailed instructions, refer to the User Manual in the 'docs' folder.

========================================
4. Environment Variables
========================================
- Before running the application, set the following environment variables:
  ETHERSCAN_API_KEY = Your Etherscan API key
  SECRET_KEY = Any random string for Flask session security

- On Windows, you can set these in the terminal:
  set ETHERSCAN_API_KEY=your_api_key_here
  set SECRET_KEY=your_secret_key_here

- Or create a .env file in the 'fyp' folder with:
  ETHERSCAN_API_KEY=your_api_key_here
  SECRET_KEY=your_secret_key_here

========================================
5. Initializing the Database
========================================
- Run the following command in the terminal (inside the 'fyp' folder):
  flask init-db

- This will create the necessary database tables for the application.

========================================
6. Support
========================================
- For any issues, refer to the documentation in the 'docs' folder.
- Contact the project author for further assistance.

========================================
Thank you for using EtherGuard!
