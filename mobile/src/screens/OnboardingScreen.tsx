import React, { useState, useEffect } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, ActivityIndicator, ScrollView, Alert, SafeAreaView } from 'react-native';
import client from '../api/client';

export default function OnboardingScreen({ navigation }: any) {
  const [fields, setFields] = useState<any[]>([]);
  const [formData, setFormData] = useState<any>({});
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    fetchFields();
  }, []);

  const fetchFields = async () => {
    try {
      const res = await client.get('users/onboarding-fields/');
      setFields(res.data);
      // Initialize form data
      const initialData: any = {};
      res.data.forEach((f: any) => {
        initialData[f.name] = '';
      });
      setFormData(initialData);
    } catch (err) {
      console.error(err);
      Alert.alert('Error', 'Failed to load onboarding fields.');
    } finally {
      setLoading(false);
    }
  };

  const handleInputChange = (name: string, value: string) => {
    setFormData({ ...formData, [name]: value });
  };

  const handleSubmit = async () => {
    // Basic validation
    for (const f of fields) {
      if (f.required && !formData[f.name]) {
        Alert.alert('Error', `Please fill out the ${f.label} field.`);
        return;
      }
    }

    setSubmitting(true);
    try {
      await client.post('users/save-profile/', formData);
      Alert.alert('Success', 'Profile saved successfully!');
      navigation.replace('MainTabs');
    } catch (err) {
      console.error(err);
      Alert.alert('Error', 'Failed to save profile.');
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <View style={[styles.container, styles.centered]}>
        <ActivityIndicator color="#facc15" size="large" />
      </View>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Complete Your Profile</Text>
        <Text style={styles.headerSubtitle}>Please fill in a few details to get started.</Text>
      </View>

      <ScrollView contentContainerStyle={styles.formContainer}>
        {fields.map((field) => (
          <View key={field.name} style={styles.inputGroup}>
            <Text style={styles.label}>
              {field.label} {field.required && <Text style={{ color: 'red' }}>*</Text>}
            </Text>
            <TextInput
              style={styles.input}
              placeholder={`Enter ${field.label}`}
              placeholderTextColor="#666"
              value={formData[field.name]}
              onChangeText={(val) => handleInputChange(field.name, val)}
              multiline={field.type === 'textarea'}
            />
          </View>
        ))}

        <TouchableOpacity style={styles.button} onPress={handleSubmit} disabled={submitting}>
          {submitting ? <ActivityIndicator color="#000" /> : <Text style={styles.buttonText}>Save Profile</Text>}
        </TouchableOpacity>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#050505' },
  centered: { justifyContent: 'center', alignItems: 'center' },
  header: { padding: 20, paddingTop: 40, borderBottomWidth: 1, borderBottomColor: '#27272a' },
  headerTitle: { color: '#fff', fontSize: 28, fontWeight: 'bold' },
  headerSubtitle: { color: '#a1a1aa', fontSize: 16, marginTop: 8 },
  formContainer: { padding: 20 },
  inputGroup: { marginBottom: 20 },
  label: { color: '#fff', fontSize: 16, marginBottom: 8, fontWeight: '500' },
  input: { 
    backgroundColor: '#18181b', 
    color: '#fff', 
    borderRadius: 8, 
    padding: 16, 
    fontSize: 16, 
    borderWidth: 1, 
    borderColor: '#27272a' 
  },
  button: { 
    backgroundColor: '#facc15', 
    borderRadius: 8, 
    padding: 16, 
    alignItems: 'center', 
    marginTop: 20,
    marginBottom: 40
  },
  buttonText: { color: '#000', fontSize: 18, fontWeight: 'bold' },
});
