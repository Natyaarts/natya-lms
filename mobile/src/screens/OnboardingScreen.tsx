import React, { useState, useEffect } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, ActivityIndicator, ScrollView, Alert, SafeAreaView, Modal } from 'react-native';
import client from '../api/client';

export default function OnboardingScreen({ navigation }: any) {
  const [fields, setFields] = useState<any[]>([]);
  const [formData, setFormData] = useState<any>({});
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  
  // Custom dropdown selection modal state
  const [activeDropdownField, setActiveDropdownField] = useState<any>(null);

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
        if (f.type === 'checkbox') {
          initialData[f.name] = false;
        } else {
          initialData[f.name] = '';
        }
      });
      setFormData(initialData);
    } catch (err) {
      console.error(err);
      Alert.alert('Error', 'Failed to load onboarding fields.');
    } finally {
      setLoading(false);
    }
  };

  const handleInputChange = (name: string, value: any) => {
    setFormData((prev: any) => ({ ...prev, [name]: value }));
  };

  const handleDateChange = (name: string, text: string) => {
    // Remove non-numeric characters
    const cleaned = text.replace(/[^0-9]/g, '');
    let formatted = cleaned;
    
    // Auto format: YYYY-MM-DD
    if (cleaned.length > 4) {
      formatted = cleaned.substring(0, 4) + '-' + cleaned.substring(4);
    }
    if (cleaned.length > 6) {
      formatted = formatted.substring(0, 7) + '-' + cleaned.substring(6, 8);
    }
    
    handleInputChange(name, formatted);
  };

  const handleSubmit = async () => {
    // Basic validation
    for (const f of fields) {
      if (f.required) {
        const val = formData[f.name];
        if (f.type === 'checkbox' && val !== true) {
          Alert.alert('Error', `Please accept the checkbox for: ${f.label}`);
          return;
        }
        if (f.type !== 'checkbox' && (!val || val.toString().trim() === '')) {
          Alert.alert('Error', `Please fill out the ${f.label} field.`);
          return;
        }
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
            
            {/* TEXT INPUT */}
            {field.type === 'text' && (
              <TextInput
                style={styles.input}
                placeholder={`Enter ${field.label}`}
                placeholderTextColor="#666"
                value={formData[field.name]}
                onChangeText={(val) => handleInputChange(field.name, val)}
              />
            )}

            {/* TEXTAREA INPUT */}
            {field.type === 'textarea' && (
              <TextInput
                style={[styles.input, styles.textarea]}
                placeholder={`Enter ${field.label}`}
                placeholderTextColor="#666"
                value={formData[field.name]}
                onChangeText={(val) => handleInputChange(field.name, val)}
                multiline={true}
                numberOfLines={4}
                textAlignVertical="top"
              />
            )}

            {/* DATE INPUT */}
            {field.type === 'date' && (
              <TextInput
                style={styles.input}
                placeholder="YYYY-MM-DD"
                placeholderTextColor="#666"
                value={formData[field.name]}
                onChangeText={(val) => handleDateChange(field.name, val)}
                keyboardType="numeric"
                maxLength={10}
              />
            )}

            {/* DROPDOWN SELECT INPUT */}
            {field.type === 'dropdown' && (
              <TouchableOpacity
                style={styles.dropdownButton}
                activeOpacity={0.8}
                onPress={() => setActiveDropdownField(field)}
              >
                <Text style={formData[field.name] ? styles.dropdownButtonText : styles.dropdownButtonPlaceholder}>
                  {formData[field.name] || `Select ${field.label}`}
                </Text>
                <Text style={styles.dropdownArrow}>▼</Text>
              </TouchableOpacity>
            )}

            {/* CHECKBOX INPUT */}
            {field.type === 'checkbox' && (
              <TouchableOpacity
                style={styles.checkboxRow}
                activeOpacity={0.8}
                onPress={() => handleInputChange(field.name, !formData[field.name])}
              >
                <View style={[
                  styles.checkboxBox,
                  formData[field.name] && styles.checkboxBoxChecked
                ]}>
                  {formData[field.name] && <Text style={styles.checkboxCheckmark}>✓</Text>}
                </View>
                <Text style={styles.checkboxLabel}>Yes, I agree</Text>
              </TouchableOpacity>
            )}
          </View>
        ))}

        <TouchableOpacity style={styles.button} onPress={handleSubmit} disabled={submitting}>
          {submitting ? <ActivityIndicator color="#000" /> : <Text style={styles.buttonText}>Save Profile</Text>}
        </TouchableOpacity>
      </ScrollView>

      {/* DROPDOWN OPTIONS LIST MODAL */}
      {activeDropdownField && (
        <Modal
          visible={!!activeDropdownField}
          transparent={true}
          animationType="fade"
          onRequestClose={() => setActiveDropdownField(null)}
        >
          <View style={styles.modalOverlay}>
            <View style={styles.modalContent}>
              <Text style={styles.modalTitle}>Select {activeDropdownField.label}</Text>
              <ScrollView style={styles.modalList} showsVerticalScrollIndicator={false}>
                {activeDropdownField.options && activeDropdownField.options.map((opt: string) => (
                  <TouchableOpacity
                    key={opt}
                    style={[
                      styles.optionItem,
                      formData[activeDropdownField.name] === opt && styles.optionItemActive
                    ]}
                    onPress={() => {
                      handleInputChange(activeDropdownField.name, opt);
                      setActiveDropdownField(null);
                    }}
                  >
                    <Text style={[
                      styles.optionText,
                      formData[activeDropdownField.name] === opt && styles.optionTextActive
                    ]}>
                      {opt}
                    </Text>
                  </TouchableOpacity>
                ))}
              </ScrollView>
              <TouchableOpacity
                style={styles.closeModalButton}
                onPress={() => setActiveDropdownField(null)}
              >
                <Text style={styles.closeModalText}>Cancel</Text>
              </TouchableOpacity>
            </View>
          </View>
        </Modal>
      )}
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
  textarea: {
    height: 100,
    paddingTop: 16
  },
  dropdownButton: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: '#18181b',
    borderRadius: 8,
    padding: 16,
    borderWidth: 1,
    borderColor: '#27272a'
  },
  dropdownButtonText: {
    color: '#fff',
    fontSize: 16
  },
  dropdownButtonPlaceholder: {
    color: '#666',
    fontSize: 16
  },
  dropdownArrow: {
    color: '#a1a1aa',
    fontSize: 12
  },
  checkboxRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 4
  },
  checkboxBox: {
    width: 24,
    height: 24,
    borderRadius: 6,
    borderWidth: 2,
    borderColor: '#27272a',
    backgroundColor: '#18181b',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 12
  },
  checkboxBoxChecked: {
    backgroundColor: '#facc15',
    borderColor: '#facc15'
  },
  checkboxCheckmark: {
    color: '#000',
    fontSize: 14,
    fontWeight: 'bold'
  },
  checkboxLabel: {
    color: '#e4e4e7',
    fontSize: 16
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

  // Modal styles
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.8)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 20
  },
  modalContent: {
    backgroundColor: '#18181b',
    width: '100%',
    maxHeight: '70%',
    borderRadius: 16,
    padding: 20,
    borderWidth: 1,
    borderColor: '#27272a'
  },
  modalTitle: {
    color: '#fff',
    fontSize: 20,
    fontWeight: 'bold',
    marginBottom: 16,
    textAlign: 'center'
  },
  modalList: {
    marginBottom: 16
  },
  optionItem: {
    paddingVertical: 14,
    paddingHorizontal: 16,
    borderRadius: 8,
    marginBottom: 8,
    backgroundColor: '#27272a'
  },
  optionItemActive: {
    backgroundColor: 'rgba(250, 204, 21, 0.1)',
    borderWidth: 1,
    borderColor: '#facc15'
  },
  optionText: {
    color: '#a1a1aa',
    fontSize: 16,
    textAlign: 'center'
  },
  optionTextActive: {
    color: '#facc15',
    fontWeight: 'bold'
  },
  closeModalButton: {
    paddingVertical: 12,
    borderTopWidth: 1,
    borderTopColor: '#27272a'
  },
  closeModalText: {
    color: '#facc15',
    fontSize: 16,
    fontWeight: 'bold',
    textAlign: 'center'
  }
});
