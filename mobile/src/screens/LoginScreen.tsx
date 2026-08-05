import React, { useState } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, ActivityIndicator, Alert, SafeAreaView, Image, Linking } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import client from '../api/client';

export default function LoginScreen({ navigation }: any) {
  const [step, setStep] = useState<'PHONE' | 'OTP'>('PHONE');
  const [phone, setPhone] = useState('');
  const [otp, setOtp] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSendOtp = async () => {
    const trimmedPhone = phone.trim();
    if (trimmedPhone.length < 10) return Alert.alert("Error", "Please enter a valid phone number");
    setLoading(true);
    try {
      await client.post('users/send-otp/', { identifier: '+91' + trimmedPhone });
      setStep('OTP');
    } catch (err: any) {
      Alert.alert("Error", err.response?.data?.error || "Failed to send OTP");
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyOtp = async () => {
    const trimmedPhone = phone.trim();
    const trimmedOtp = otp.trim();
    if (trimmedOtp.length !== 4 && trimmedOtp.length !== 6) return Alert.alert("Error", "Enter correct OTP");
    setLoading(true);
    try {
      const res = await client.post('users/verify-otp/', { identifier: '+91' + trimmedPhone, otp: trimmedOtp });
      if (res.status === 200 || res.status === 201) {
        if (res.data.tokens) {
          await AsyncStorage.setItem('access_token', res.data.tokens.access);
          await AsyncStorage.setItem('refresh_token', res.data.tokens.refresh);
          if (res.data.is_onboarded) {
            navigation.replace('MainTabs');
          } else {
            navigation.replace('Onboarding');
          }
        }
      } else {
        Alert.alert("Error", "Invalid verification response");
      }
    } catch (err: any) {
      Alert.alert("Error", err.response?.data?.error || "Invalid OTP");
    } finally {
      setLoading(false);
    }
  };

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.content}>
        <View style={styles.logoContainer}>
          <Image source={require('../../assets/logo.png')} style={styles.logoImage} />
        </View>
        <Text style={styles.title}>Learning Hub</Text>
        <Text style={styles.subtitle}>Welcome back! Sign in to continue your learning journey.</Text>

        {step === 'PHONE' ? (
          <>
            <View style={styles.inputContainer}>
              <Text style={styles.prefix}>+91</Text>
              <TextInput
                style={styles.input}
                placeholder="Mobile Number"
                placeholderTextColor="#666"
                keyboardType="phone-pad"
                value={phone}
                onChangeText={setPhone}
                maxLength={10}
              />
            </View>
            <TouchableOpacity style={styles.button} onPress={handleSendOtp} disabled={loading}>
              {loading ? <ActivityIndicator color="#000" /> : <Text style={styles.buttonText}>Send OTP</Text>}
            </TouchableOpacity>

            <View style={styles.divider}>
              <View style={styles.dividerLine} />
              <Text style={styles.dividerText}>OR</Text>
              <View style={styles.dividerLine} />
            </View>

            <TouchableOpacity style={styles.secondaryButton} onPress={async () => {
              try {
                // Ensure Google Sign-In is configured
                // Requires configuring Web Client ID in Google Cloud Console
                const { GoogleSignin, statusCodes } = require('@react-native-google-signin/google-signin');
                GoogleSignin.configure({
                  webClientId: 'YOUR_WEB_CLIENT_ID_HERE.apps.googleusercontent.com', // TODO: REPLACE THIS
                });
                await GoogleSignin.hasPlayServices();
                const userInfo = await GoogleSignin.signIn();
                const idToken = userInfo.data?.idToken;
                
                if (idToken) {
                  setLoading(true);
                  const res = await client.post('users/mobile-google-login/', { token: idToken });
                  if (res.data.tokens) {
                    await AsyncStorage.setItem('access_token', res.data.tokens.access);
                    await AsyncStorage.setItem('refresh_token', res.data.tokens.refresh);
                    if (res.data.is_onboarded) {
                      navigation.replace('MainTabs');
                    } else {
                      navigation.replace('Onboarding');
                    }
                  }
                }
              } catch (error: any) {
                console.error(error);
                Alert.alert('Error', 'Google Sign-In failed or was cancelled');
              } finally {
                setLoading(false);
              }
            }}>
              <Text style={styles.secondaryButtonText}>Continue with Google</Text>
            </TouchableOpacity>

            <View style={styles.footer}>
              <Text style={styles.footerText}>Don't have an account? </Text>
              <TouchableOpacity onPress={() => Linking.openURL('https://academy.natyaarts.com/register')}>
                <Text style={styles.signupText}>Sign up</Text>
              </TouchableOpacity>
            </View>
          </>
        ) : (
          <>
            <Text style={styles.label}>Enter OTP sent to +91 {phone}</Text>
            <TextInput
              style={styles.otpInput}
              placeholder="000000"
              placeholderTextColor="#666"
              keyboardType="number-pad"
              value={otp}
              onChangeText={setOtp}
              maxLength={6}
              secureTextEntry
            />
            <TouchableOpacity style={styles.button} onPress={handleVerifyOtp} disabled={loading}>
              {loading ? <ActivityIndicator color="#000" /> : <Text style={styles.buttonText}>Verify & Login</Text>}
            </TouchableOpacity>
            <TouchableOpacity style={styles.textButton} onPress={() => setStep('PHONE')} disabled={loading}>
              <Text style={styles.textButtonText}>Change Phone Number</Text>
            </TouchableOpacity>
          </>
        )}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#050505', justifyContent: 'center' },
  content: { padding: 24 },
  logoContainer: { alignItems: 'center', marginBottom: 16 },
  logoImage: { width: 240, height: 240, resizeMode: 'contain' },
  title: { fontSize: 32, fontWeight: 'bold', color: '#fff', marginBottom: 8, textAlign: 'center', marginTop: 12 },
  subtitle: { fontSize: 16, color: '#a1a1aa', marginBottom: 32, textAlign: 'center', lineHeight: 24 },
  inputContainer: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#18181b', borderRadius: 12, borderWidth: 1, borderColor: '#27272a', marginBottom: 24 },
  prefix: { paddingHorizontal: 16, fontSize: 18, color: '#a1a1aa', borderRightWidth: 1, borderRightColor: '#27272a' },
  input: { flex: 1, padding: 16, fontSize: 18, color: '#fff' },
  otpInput: { backgroundColor: '#18181b', borderRadius: 12, borderWidth: 1, borderColor: '#27272a', padding: 16, fontSize: 24, color: '#fff', textAlign: 'center', letterSpacing: 8, marginBottom: 24 },
  button: { backgroundColor: '#facc15', padding: 16, borderRadius: 12, alignItems: 'center' },
  buttonText: { color: '#000', fontSize: 16, fontWeight: '600' },
  textButton: { marginTop: 16, alignItems: 'center', padding: 8 },
  textButtonText: { color: '#facc15', fontSize: 14 },
  label: { color: '#a1a1aa', marginBottom: 12, textAlign: 'center' },
  divider: { flexDirection: 'row', alignItems: 'center', marginVertical: 24 },
  dividerLine: { flex: 1, height: 1, backgroundColor: '#27272a' },
  dividerText: { color: '#52525b', paddingHorizontal: 16, fontSize: 12, fontWeight: 'bold' },
  secondaryButton: { backgroundColor: '#18181b', padding: 16, borderRadius: 12, alignItems: 'center', marginBottom: 12, borderWidth: 1, borderColor: '#27272a' },
  secondaryButtonText: { color: '#e4e4e7', fontSize: 16, fontWeight: '500' },
  footer: { flexDirection: 'row', justifyContent: 'center', marginTop: 24 },
  footerText: { color: '#a1a1aa', fontSize: 14 },
  signupText: { color: '#facc15', fontSize: 14, fontWeight: 'bold' },
});
