import React from 'react';
import { StyleSheet, Text, View, StatusBar } from 'react-native';

export default function App() {
  return (
    <View style={styles.container}>
      <StatusBar style="auto" />
      <Text style={styles.title}>🎉 PhunParty</Text>
      <Text style={styles.subtitle}>Player App</Text>
      <View style={styles.statusContainer}>
        <Text style={styles.status} testID="app-status">
          Coming Soon...
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#fff',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 20,
  },
  title: {
    fontSize: 32,
    fontWeight: 'bold',
    marginBottom: 10,
  },
  subtitle: {
    fontSize: 18,
    color: '#666',
    marginBottom: 30,
  },
  statusContainer: {
    padding: 20,
    backgroundColor: '#f0f0f0',
    borderRadius: 10,
  },
  status: {
    fontSize: 16,
    textAlign: 'center',
  },
});
