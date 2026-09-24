import React, { useState } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, ActivityIndicator, Alert, Image, Linking, Platform } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import AsyncStorage from '@react-native-async-storage/async-storage';
import client from '../api/client';
import { isExpoGo } from '../api/pushNotifications';
import { colors, radius } from '../theme';
import { Country, DEFAULT_COUNTRY, flagEmoji } from '../data/countries';
import CountryPicker from '../components/CountryPicker';
import Icon from '../components/Icon';

export default function LoginScreen({ navigation }: any) {
  const [step, setStep] = useState<'PHONE' | 'OTP'>('PHONE');
  const [country, setCountry] = useState<Country>(DEFAULT_COUNTRY);
  const [showCountryPicker, setShowCountryPicker] = useState(false);
  const [phone, setPhone] = useState('');
  const [otp, setOtp] = useState('');
  const [loading, setLoading] = useState(false);

  // India keeps its existing, already-working 10-digit expectation exactly
  // as before (never regress the primary user base); any other country
  // just needs a plausible minimum length -- the backend itself performs
  // no stricter format validation than that (see users/views.py).
  const minPhoneLength = country.iso2 === 'IN' ? 10 : 6;
  const maxPhoneLength = country.iso2 === 'IN' ? 10 : 15;

  // Robust phone sanitization:
  // 1. Strips non-digits.
  // 2. If the user pasted a number that already contains the selected dial
  //    code (e.g. pasted "+919876543210" or "919876543210" while India is
  //    selected), strip the redundant prefix so we never send "+91919876543210".
  // 3. Strips any leading trunk zero (e.g. "09876543210" -> "9876543210").
  const sanitizePhoneNumber = (raw: string, dialCode: string): string => {
    let digits = raw.trim().replace(/[^0-9]/g, '');
    const dialDigits = dialCode.replace(/[^0-9]/g, '');
    if (dialDigits && digits.startsWith(dialDigits) && digits.length > dialDigits.length + 5) {
      digits = digits.slice(dialDigits.length);
    }
    digits = digits.replace(/^0+/, '');
    return digits;
  };

  const getCleanPhone = () => sanitizePhoneNumber(phone, country.dialCode);
  const buildIdentifier = () => country.dialCode + getCleanPhone();

  const handleSendOtp = async () => {
    const cleanPhone = getCleanPhone();
    if (cleanPhone.length < minPhoneLength) {
      return Alert.alert("Error", country.iso2 === 'IN' ? "Please enter a valid 10-digit mobile number" : "Please enter a valid phone number");
    }
    if (country.iso2 === 'IN' && cleanPhone.length !== 10) {
      return Alert.alert("Error", "Please enter a valid 10-digit mobile number");
    }
    setLoading(true);
    try {
      await client.post('users/send-otp/', { identifier: buildIdentifier() });
      setStep('OTP');
    } catch (err: any) {
      Alert.alert("Error", err.response?.data?.error || "Failed to send OTP");
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyOtp = async () => {
    const trimmedOtp = otp.trim();
    if (trimmedOtp.length !== 4 && trimmedOtp.length !== 6) return Alert.alert("Error", "Enter correct OTP");
    setLoading(true);
    try {
      const res = await client.post('users/verify-otp/', { identifier: buildIdentifier(), otp: trimmedOtp });
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
              <TouchableOpacity
                key={country.iso2}
                style={styles.countrySelector}
                onPress={() => setShowCountryPicker(true)}
                accessibilityRole="button"
                accessibilityLabel={`Country: ${country.name}, dial code ${country.dialCode}`}
              >
                <Text style={styles.flag}>{flagEmoji(country.iso2)}</Text>
                <Text style={styles.prefix}>{country.dialCode}</Text>
                <Icon name="chevron-down" size={14} color={colors.textSecondary} />
              </TouchableOpacity>
              <TextInput
                style={styles.input}
                placeholder="Mobile Number"
                placeholderTextColor="#666"
                keyboardType="phone-pad"
                value={phone}
                onChangeText={setPhone}
                maxLength={maxPhoneLength}
              />
            </View>

            <CountryPicker
              visible={showCountryPicker}
              selected={country}
              onSelect={(c) => {
                setCountry(c);
                setShowCountryPicker(false);
              }}
              onClose={() => setShowCountryPicker(false)}
            />
            <TouchableOpacity style={styles.button} onPress={handleSendOtp} disabled={loading}>
              {loading ? <ActivityIndicator color={colors.textInverse} /> : <Text style={styles.buttonText}>Send OTP</Text>}
            </TouchableOpacity>

            {Platform.OS === 'android' && (
              <>
                <View style={styles.divider}>
                  <View style={styles.dividerLine} />
                  <Text style={styles.dividerText}>OR</Text>
                  <View style={styles.dividerLine} />
                </View>

                <TouchableOpacity
                  style={styles.secondaryButton}
                  onPress={async () => {
                    // Stage 3B: Google Sign-In is strictly Android-only; prevent any iOS execution
                    if (Platform.OS !== 'android') return;

                    // In Expo Go, the custom native module RNGoogleSignin is not linked in the binary.
                    // Guard against importing or calling GoogleSignin in Expo Go so it never crashes.
                    if (isExpoGo) {
                      Alert.alert(
                        'Development Build Required',
                        'Google Sign-In requires custom native modules that are not available in Expo Go. Please use Phone/OTP login, or run the app using a development build (APK).'
                      );
                      return;
                    }

                    let GoogleSignin: any;
                    let statusCodes: any;
                    let isErrorWithCode: any;
                    let isCancelledResponse: any;

                    try {
                      const gSignin = require('@react-native-google-signin/google-signin');
                      GoogleSignin = gSignin?.GoogleSignin;
                      statusCodes = gSignin?.statusCodes;
                      isErrorWithCode = gSignin?.isErrorWithCode;
                      isCancelledResponse = gSignin?.isCancelledResponse;
                      if (!GoogleSignin) {
                        throw new Error('GoogleSignin native module is undefined');
                      }
                    } catch (loadError: any) {
                      console.warn('Failed to load GoogleSignin native module:', loadError);
                      Alert.alert(
                        'Google Sign-In Unavailable',
                        'Native Google Sign-In is not supported in this build. Please use phone/OTP login instead.'
                      );
                      return;
                    }

                    try {
                      const webClientId = process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID;
                      if (!webClientId) {
                        Alert.alert(
                          'Google Sign-In unavailable',
                          'This build is not configured with a Google Web Client ID. Please configure EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID or use phone/OTP login.'
                        );
                        return;
                      }
                      GoogleSignin.configure({
                        webClientId: webClientId.trim(),
                        scopes: ['profile', 'email'],
                      });
                      await GoogleSignin.hasPlayServices({ showPlayServicesUpdateDialog: true });
                      const userInfo = await GoogleSignin.signIn();

                      // In @react-native-google-signin/google-signin v13+, cancelling returns { type: 'cancelled', data: null }
                      if (
                        (isCancelledResponse && isCancelledResponse(userInfo)) ||
                        (userInfo as any)?.type === 'cancelled'
                      ) {
                        return;
                      }

                      const idToken = userInfo?.data?.idToken || (userInfo as any)?.idToken;

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
                      } else {
                        Alert.alert('Google Sign-In failed', 'Google did not return a valid sign-in token. Please try again or use phone/OTP login.');
                      }
                    } catch (error: any) {
                      console.error('Google Sign-In error:', error);
                      const errorCode = String(error?.code || '');
                      const errorMsg = String(error?.message || '');

                      if (isErrorWithCode && isErrorWithCode(error)) {
                        if (error.code === statusCodes?.SIGN_IN_CANCELLED || error.code === statusCodes?.IN_PROGRESS) {
                          // User backed out, or a sign-in is already underway -- no alert needed.
                          return;
                        } else if (error.code === statusCodes?.PLAY_SERVICES_NOT_AVAILABLE) {
                          Alert.alert('Google Play Services required', 'Google Sign-In needs Google Play Services, which isn\'t available or up to date on this device. Please use phone/OTP login instead.');
                          return;
                        }
                      }

                      // DEVELOPER_ERROR / code 10: SHA-1 fingerprint or package mismatch in Google Cloud Console
                      if (errorCode === '10' || errorMsg.includes('DEVELOPER_ERROR')) {
                        Alert.alert(
                          'Google Sign-In Configuration Error',
                          'Developer Error (code 10): Ensure this app\'s SHA-1 certificate fingerprint and package name (com.natyaarts.academy) are registered under Android OAuth clients in Google Cloud Console.'
                        );
                        return;
                      }

                      Alert.alert(
                        'Google Sign-In unavailable',
                        __DEV__ && errorMsg
                          ? `Google Sign-In error: ${errorMsg}`
                          : 'Google Sign-In couldn\'t complete on this device. Please use phone/OTP login instead.'
                      );
                    } finally {
                      setLoading(false);
                    }
                  }}
                >
                  <Text style={styles.secondaryButtonText}>Continue with Google</Text>
                </TouchableOpacity>
                {isExpoGo && (
                  <Text style={styles.expoGoNote}>
                    Google Sign-In requires a development build. Use phone/OTP in Expo Go.
                  </Text>
                )}
              </>
            )}

            <View style={styles.footer}>
              <Text style={styles.footerText}>Don't have an account? </Text>
              <TouchableOpacity onPress={() => Linking.openURL('https://academy.natyaarts.com/register')}>
                <Text style={styles.signupText}>Sign up</Text>
              </TouchableOpacity>
            </View>
          </>
        ) : (
          <>
            <Text style={styles.label}>Enter OTP sent to {country.dialCode} {getCleanPhone()}</Text>
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
              {loading ? <ActivityIndicator color={colors.textInverse} /> : <Text style={styles.buttonText}>Verify & Login</Text>}
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
  container: { flex: 1, backgroundColor: colors.bg, justifyContent: 'center' },
  content: { padding: 24 },
  logoContainer: { alignItems: 'center', marginBottom: 16 },
  logoImage: { width: 240, height: 240, resizeMode: 'contain' },
  title: { fontSize: 30, fontWeight: '700', color: colors.text, marginBottom: 8, textAlign: 'center', marginTop: 12 },
  subtitle: { fontSize: 15, color: colors.textSecondary, marginBottom: 32, textAlign: 'center', lineHeight: 22 },
  inputContainer: { flexDirection: 'row', alignItems: 'center', backgroundColor: colors.card, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, marginBottom: 24 },
  // No fixed width -- the row sizes itself to flag + dial code + chevron,
  // shrinks/grows safely with longer dial codes (e.g. +971) and Android's
  // font-scale settings (capped via maxFontSizeMultiplier on the Text
  // itself) instead of clipping on smaller screens.
  countrySelector: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
    paddingHorizontal: 12, paddingVertical: 16,
    borderRightWidth: 1, borderRightColor: colors.border,
  },
  flag: { fontSize: 18, marginRight: 6 },
  prefix: { fontSize: 16, fontWeight: '600', color: colors.text, marginRight: 6 },
  input: { flex: 1, paddingHorizontal: 16, paddingVertical: 16, fontSize: 18, color: colors.text },
  otpInput: { backgroundColor: colors.card, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, padding: 16, fontSize: 24, color: colors.text, textAlign: 'center', letterSpacing: 8, marginBottom: 24 },
  button: { backgroundColor: colors.accent, padding: 16, borderRadius: radius.md, alignItems: 'center' },
  buttonText: { color: colors.textInverse, fontSize: 16, fontWeight: '600' },
  textButton: { marginTop: 16, alignItems: 'center', padding: 8 },
  textButtonText: { color: colors.accent, fontSize: 14 },
  label: { color: colors.textSecondary, marginBottom: 12, textAlign: 'center' },
  divider: { flexDirection: 'row', alignItems: 'center', marginVertical: 24 },
  dividerLine: { flex: 1, height: 1, backgroundColor: colors.border },
  dividerText: { color: colors.textTertiary, paddingHorizontal: 16, fontSize: 12, fontWeight: '700' },
  secondaryButton: { backgroundColor: colors.card, padding: 16, borderRadius: radius.md, alignItems: 'center', marginBottom: 12, borderWidth: 1, borderColor: colors.border },
  secondaryButtonText: { color: colors.text, fontSize: 16, fontWeight: '500' },
  expoGoNote: { color: colors.textTertiary, fontSize: 12, textAlign: 'center', marginTop: -4, marginBottom: 12 },
  footer: { flexDirection: 'row', justifyContent: 'center', marginTop: 24 },
  footerText: { color: colors.textSecondary, fontSize: 14 },
  signupText: { color: colors.accent, fontSize: 14, fontWeight: '700' },
});
