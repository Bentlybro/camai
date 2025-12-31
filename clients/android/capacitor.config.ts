import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.camai.app',
  appName: 'CAMAI',
  webDir: 'www',
  server: {
    androidScheme: 'http',
    cleartext: true // Allow HTTP for local network connections
  },
  plugins: {
    SplashScreen: {
      launchShowDuration: 2000,
      backgroundColor: '#1a1a2e',
      showSpinner: true,
      spinnerColor: '#00d4ff'
    },
    StatusBar: {
      style: 'DARK',
      backgroundColor: '#1a1a2e'
    }
  },
  android: {
    allowMixedContent: true // Allow HTTP streams on HTTPS pages
  }
};

export default config;
