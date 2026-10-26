import React, { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  ScrollView,
  Alert,
  Modal,
  KeyboardAvoidingView,
  Platform,
  Keyboard,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import client from '../api/client';
import Icon from '../components/Icon';
import { colors, radius, spacing, typography } from '../theme';

type OnboardingStep = 'TRACK' | 'DETAILS';

interface FieldConfig {
  name: string;
  label: string;
  type: 'text' | 'textarea' | 'date' | 'dropdown' | 'checkbox';
  required: boolean;
  options?: string[];
  order?: number;
}

export default function OnboardingScreen({ navigation }: any) {
  const [step, setStep] = useState<OnboardingStep>('TRACK');
  const [selectedTrack, setSelectedTrack] = useState<'CAREER_ACADEMY' | 'REGULAR' | null>(null);

  const [fields, setFields] = useState<FieldConfig[]>([]);
  const [formData, setFormData] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [keyboardHeight, setKeyboardHeight] = useState(0);

  // Searchable dropdown modal
  const [activeDropdownField, setActiveDropdownField] = useState<FieldConfig | null>(null);
  const [dropdownSearch, setDropdownSearch] = useState('');

  const scrollViewRef = useRef<ScrollView>(null);
  const fieldOffsets = useRef<Record<string, number>>({});

  useEffect(() => {
    const showSub = Keyboard.addListener(
      Platform.OS === 'ios' ? 'keyboardWillShow' : 'keyboardDidShow',
      (e: any) => setKeyboardHeight(e.endCoordinates.height)
    );
    const hideSub = Keyboard.addListener(
      Platform.OS === 'ios' ? 'keyboardWillHide' : 'keyboardDidHide',
      () => setKeyboardHeight(0)
    );
    return () => {
      showSub.remove();
      hideSub.remove();
    };
  }, []);

  const handleFieldFocus = (fieldName: string) => {
    const y = fieldOffsets.current[fieldName];
    if (typeof y === 'number' && scrollViewRef.current) {
      setTimeout(() => {
        scrollViewRef.current?.scrollTo({
          y: Math.max(0, y - 24),
          animated: true,
        });
      }, 120);
    }
  };

  useEffect(() => {
    loadInitialData();
  }, []);

  const loadInitialData = async () => {
    try {
      setLoading(true);
      // Fetch fields and current user in parallel
      const [fieldsRes, userRes] = await Promise.allSettled([
        client.get('users/onboarding-fields/'),
        client.get('users/me/'),
      ]);

      let initialData: Record<string, any> = {};

      if (userRes.status === 'fulfilled' && userRes.value.data) {
        const u = userRes.value.data;
        if (u.student_type) {
          if (u.student_type === 'CAREER_ACADEMY' || u.student_type === 'REGULAR') {
            setSelectedTrack(u.student_type);
          }
        }
        initialData.full_name = [u.first_name, u.last_name].filter(Boolean).join(' ');
        initialData.email = u.email || '';
        initialData.phone_number = u.phone_number || '';
        initialData.state = u.state || '';
        initialData.district = u.district || '';
        initialData.pincode = u.pincode || '';

        if (u.onboarding_data && typeof u.onboarding_data === 'object') {
          initialData = { ...initialData, ...u.onboarding_data };
        }
      }

      if (fieldsRes.status === 'fulfilled' && Array.isArray(fieldsRes.value.data)) {
        const loadedFields: FieldConfig[] = fieldsRes.value.data;
        setFields(loadedFields);

        loadedFields.forEach((f) => {
          if (f.name === 'student_type') return; // Handled in Step 1
          if (initialData[f.name] === undefined) {
            initialData[f.name] = f.type === 'checkbox' ? false : '';
          }
        });
      }

      setFormData(initialData);
    } catch (err) {
      console.error('Failed to load onboarding info', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSelectTrack = (track: 'CAREER_ACADEMY' | 'REGULAR') => {
    setSelectedTrack(track);
    setFormData((prev) => ({
      ...prev,
      student_type: track,
    }));
  };

  const handleProceedToDetails = () => {
    if (!selectedTrack) {
      Alert.alert('Select Track', 'Please choose between Career Academy or Regular track to continue.');
      return;
    }
    setStep('DETAILS');
  };

  const handleInputChange = (name: string, value: any) => {
    setFormData((prev) => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async () => {
    Keyboard.dismiss();

    if (!selectedTrack) {
      Alert.alert('Error', 'Please select a learning track.');
      setStep('TRACK');
      return;
    }

    // Validate standard & dynamic fields
    const fullName = (formData.full_name || formData.name || '').trim();
    if (!fullName) {
      Alert.alert('Required', 'Please enter your Full Name.');
      return;
    }

    const email = (formData.email || '').trim();
    if (email && !email.includes('@')) {
      Alert.alert('Invalid Email', 'Please enter a valid email address.');
      return;
    }

    const state = (formData.state || '').trim();
    if (!state) {
      Alert.alert('Required', 'Please select your State.');
      return;
    }

    const district = (formData.district || '').trim();
    if (!district) {
      Alert.alert('Required', 'Please enter your District.');
      return;
    }

    const pincode = (formData.pincode || '').trim();
    if (!pincode) {
      Alert.alert('Required', 'Please enter your Pincode.');
      return;
    }

    // Dynamic fields validation
    for (const f of fields) {
      if (f.name === 'student_type') continue;
      if (f.required) {
        const val = formData[f.name];
        if (f.type === 'checkbox' && val !== true) {
          Alert.alert('Required', `Please confirm the checkbox for: ${f.label}`);
          return;
        }
        if (f.type !== 'checkbox' && (!val || val.toString().trim() === '')) {
          Alert.alert('Required', `Please fill out: ${f.label}`);
          return;
        }
      }
    }

    setSubmitting(true);
    try {
      const payload = {
        ...formData,
        student_type: selectedTrack,
      };

      await client.post('users/save-profile/', payload);
      Alert.alert('Welcome to Natya!', 'Your student profile has been created successfully.');
      navigation.replace('MainTabs');
    } catch (err: any) {
      console.error(err);
      const msg = err.response?.data?.error || err.response?.data?.detail || 'Failed to save profile. Please check your connection.';
      Alert.alert('Error', msg);
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <View style={[styles.container, styles.centered]}>
        <ActivityIndicator color={colors.accent} size="large" />
      </View>
    );
  }

  // Filter out student_type from Step 2 fields since Step 1 chooses it
  const visibleFields = fields.filter((f) => f.name !== 'student_type');

  // Filtered dropdown options for modal search
  const filteredOptions = activeDropdownField?.options
    ? activeDropdownField.options.filter((opt) =>
        opt.toLowerCase().includes(dropdownSearch.toLowerCase())
      )
    : [];

  return (
    <SafeAreaView style={styles.container} edges={['top', 'left', 'right', 'bottom']}>
      {/* Top Header / Progress Indicator */}
      <View style={styles.header}>
        <View style={styles.stepIndicatorRow}>
          <View style={[styles.stepDot, styles.stepDotActive]}>
            <Text style={styles.stepDotText}>1</Text>
          </View>
          <View style={[styles.stepLine, step === 'DETAILS' && styles.stepLineActive]} />
          <View style={[styles.stepDot, step === 'DETAILS' && styles.stepDotActive]}>
            <Text style={[styles.stepDotText, step !== 'DETAILS' && styles.stepDotTextInactive]}>2</Text>
          </View>
        </View>
        <Text style={styles.headerStepText}>
          {step === 'TRACK' ? 'STEP 1 OF 2 : CHOOSE LEARNING TRACK' : 'STEP 2 OF 2 : STUDENT DETAILS'}
        </Text>
        <Text style={styles.headerTitle}>
          {step === 'TRACK' ? 'Select Your Learning Path' : 'Complete Your Profile'}
        </Text>
        <Text style={styles.headerSubtitle}>
          {step === 'TRACK'
            ? 'Choose the path tailored to your classical dance and music journey.'
            : 'Fill in your contact and location details to finalize your enrollment.'}
        </Text>
      </View>

      {step === 'TRACK' ? (
        /* STEP 1: CAREER ACADEMY VS REGULAR */
        <ScrollView
          contentContainerStyle={styles.trackScrollContainer}
          showsVerticalScrollIndicator={false}
        >
          {/* Card 1: Career Academy */}
          <TouchableOpacity
            activeOpacity={0.85}
            style={[
              styles.trackCard,
              selectedTrack === 'CAREER_ACADEMY' && styles.trackCardActive,
            ]}
            onPress={() => handleSelectTrack('CAREER_ACADEMY')}
          >
            <View style={styles.trackCardTop}>
              <View style={[styles.trackIconWrap, styles.trackIconGold]}>
                <Icon name="award" size={26} color={colors.accent} />
              </View>
              <View style={styles.trackHeaderInfo}>
                <View style={styles.badgeRow}>
                  <Text style={styles.trackTitle}>Career Academy</Text>
                  <View style={styles.proBadge}>
                    <Text style={styles.proBadgeText}>DIPLOMA & CERTIFIED</Text>
                  </View>
                </View>
                <Text style={styles.trackTagline}>Professional Training & Career Pathway</Text>
              </View>
              <View
                style={[
                  styles.radioCircle,
                  selectedTrack === 'CAREER_ACADEMY' && styles.radioCircleActive,
                ]}
              >
                {selectedTrack === 'CAREER_ACADEMY' && <View style={styles.radioInner} />}
              </View>
            </View>

            <Text style={styles.trackDesc}>
              Structured multi-year curriculum designed for dedicated practitioners seeking formal
              accreditation, certification, personal mentorship, and performance careers.
            </Text>

            <View style={styles.highlightsContainer}>
              <View style={styles.highlightRow}>
                <Icon name="check-circle" size={16} color={colors.accent} />
                <Text style={styles.highlightText}>Recognized Diplomas & Stage Certifications</Text>
              </View>
              <View style={styles.highlightRow}>
                <Icon name="check-circle" size={16} color={colors.accent} />
                <Text style={styles.highlightText}>Personalized Guru Reviews & Assignment Feedback</Text>
              </View>
              <View style={styles.highlightRow}>
                <Icon name="check-circle" size={16} color={colors.accent} />
                <Text style={styles.highlightText}>Stage Performances, Arangetram & Career Support</Text>
              </View>
            </View>
          </TouchableOpacity>

          {/* Card 2: Regular Track */}
          <TouchableOpacity
            activeOpacity={0.85}
            style={[
              styles.trackCard,
              selectedTrack === 'REGULAR' && styles.trackCardActive,
            ]}
            onPress={() => handleSelectTrack('REGULAR')}
          >
            <View style={styles.trackCardTop}>
              <View style={[styles.trackIconWrap, styles.trackIconPurple]}>
                <Icon name="book-open" size={26} color="#A78BFA" />
              </View>
              <View style={styles.trackHeaderInfo}>
                <View style={styles.badgeRow}>
                  <Text style={styles.trackTitle}>Regular Learning</Text>
                  <View style={[styles.proBadge, styles.regularBadge]}>
                    <Text style={[styles.proBadgeText, styles.regularBadgeText]}>FLEXIBLE & HOBBY</Text>
                  </View>
                </View>
                <Text style={styles.trackTagline}>Recreational & Self-Paced Learning</Text>
              </View>
              <View
                style={[
                  styles.radioCircle,
                  selectedTrack === 'REGULAR' && styles.radioCircleActive,
                ]}
              >
                {selectedTrack === 'REGULAR' && <View style={styles.radioInner} />}
              </View>
            </View>

            <Text style={styles.trackDesc}>
              Learn classical dance and music at your own rhythm for cultural enjoyment, fitness,
              and wellness with complete schedule flexibility.
            </Text>

            <View style={styles.highlightsContainer}>
              <View style={styles.highlightRow}>
                <Icon name="check-circle" size={16} color="#A78BFA" />
                <Text style={styles.highlightText}>100% Flexible Self-Paced Schedule</Text>
              </View>
              <View style={styles.highlightRow}>
                <Icon name="check-circle" size={16} color="#A78BFA" />
                <Text style={styles.highlightText}>Explore Bharatanatyam, Kuchipudi, Vocals & More</Text>
              </View>
              <View style={styles.highlightRow}>
                <Icon name="check-circle" size={16} color="#A78BFA" />
                <Text style={styles.highlightText}>Open to All Age Groups & Skill Levels</Text>
              </View>
            </View>
          </TouchableOpacity>

          {/* Step 1 Next Button */}
          <TouchableOpacity
            style={[styles.primaryButton, !selectedTrack && styles.buttonDisabled]}
            disabled={!selectedTrack}
            onPress={handleProceedToDetails}
          >
            <Text style={styles.primaryButtonText}>Continue to Details</Text>
            <Icon name="chevron-right" size={18} color={colors.textInverse} />
          </TouchableOpacity>
        </ScrollView>
      ) : (
        /* STEP 2: PROFILE DETAILS FORM */
        <KeyboardAvoidingView
          style={styles.keyboardAvoidingView}
          behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        >
          <ScrollView
            ref={scrollViewRef}
            contentContainerStyle={[
              styles.formContainer,
              { paddingBottom: keyboardHeight > 0 ? keyboardHeight + 80 : 60 },
            ]}
            keyboardShouldPersistTaps="handled"
            keyboardDismissMode="on-drag"
            showsVerticalScrollIndicator={false}
          >
            {/* Selected Track Banner */}
            <View style={styles.selectedTrackBanner}>
              <View style={styles.bannerLeft}>
                <Icon
                  name={selectedTrack === 'CAREER_ACADEMY' ? 'award' : 'book-open'}
                  size={18}
                  color={selectedTrack === 'CAREER_ACADEMY' ? colors.accent : '#A78BFA'}
                />
                <Text style={styles.bannerText}>
                  Selected Track:{' '}
                  <Text style={styles.bannerTextBold}>
                    {selectedTrack === 'CAREER_ACADEMY' ? 'Career Academy' : 'Regular Track'}
                  </Text>
                </Text>
              </View>
              <TouchableOpacity
                onPress={() => setStep('TRACK')}
                style={styles.switchTrackBtn}
              >
                <Text style={styles.switchTrackBtnText}>Change</Text>
              </TouchableOpacity>
            </View>

            {/* Dynamic Fields */}
            {visibleFields.map((field) => (
              <View
                key={field.name}
                style={styles.inputGroup}
                onLayout={(e) => {
                  fieldOffsets.current[field.name] = e.nativeEvent.layout.y;
                }}
              >
                <Text style={styles.label}>
                  {field.label}{' '}
                  {field.required && <Text style={{ color: colors.danger }}>*</Text>}
                </Text>

                {/* TEXT INPUT */}
                {field.type === 'text' && (
                  <TextInput
                    style={styles.input}
                    placeholder={`Enter ${field.label}`}
                    placeholderTextColor="#666"
                    value={formData[field.name] || ''}
                    onChangeText={(val) => handleInputChange(field.name, val)}
                    onFocus={() => handleFieldFocus(field.name)}
                    keyboardType={
                      field.name === 'phone_number'
                        ? 'phone-pad'
                        : field.name === 'pincode'
                        ? 'numeric'
                        : field.name === 'email'
                        ? 'email-address'
                        : 'default'
                    }
                    autoCapitalize={
                      field.name === 'email'
                        ? 'none'
                        : field.name === 'full_name'
                        ? 'words'
                        : 'sentences'
                    }
                  />
                )}

                {/* TEXTAREA INPUT */}
                {field.type === 'textarea' && (
                  <TextInput
                    style={[styles.input, styles.textarea]}
                    placeholder={`Enter ${field.label}`}
                    placeholderTextColor="#666"
                    value={formData[field.name] || ''}
                    onChangeText={(val) => handleInputChange(field.name, val)}
                    onFocus={() => handleFieldFocus(field.name)}
                    multiline={true}
                    numberOfLines={3}
                    textAlignVertical="top"
                  />
                )}

                {/* DATE INPUT */}
                {field.type === 'date' && (
                  <TextInput
                    style={styles.input}
                    placeholder="YYYY-MM-DD"
                    placeholderTextColor="#666"
                    value={formData[field.name] || ''}
                    onChangeText={(val) => handleInputChange(field.name, val)}
                    onFocus={() => handleFieldFocus(field.name)}
                    keyboardType="numeric"
                    maxLength={10}
                  />
                )}

                {/* DROPDOWN SELECT INPUT */}
                {field.type === 'dropdown' && (
                  <TouchableOpacity
                    style={styles.dropdownButton}
                    activeOpacity={0.8}
                    onPress={() => {
                      setDropdownSearch('');
                      setActiveDropdownField(field);
                    }}
                  >
                    <Text
                      style={
                        formData[field.name]
                          ? styles.dropdownButtonText
                          : styles.dropdownButtonPlaceholder
                      }
                      numberOfLines={1}
                    >
                      {formData[field.name] || `Select ${field.label}`}
                    </Text>
                    <Icon name="chevron-down" size={16} color={colors.textSecondary} />
                  </TouchableOpacity>
                )}

                {/* CHECKBOX INPUT */}
                {field.type === 'checkbox' && (
                  <TouchableOpacity
                    style={styles.checkboxRow}
                    activeOpacity={0.8}
                    onPress={() => handleInputChange(field.name, !formData[field.name])}
                  >
                    <View
                      style={[
                        styles.checkboxBox,
                        formData[field.name] && styles.checkboxBoxChecked,
                      ]}
                    >
                      {formData[field.name] && <Icon name="check" size={14} color="#000" />}
                    </View>
                    <Text style={styles.checkboxLabel}>I confirm this detail</Text>
                  </TouchableOpacity>
                )}
              </View>
            ))}

            {/* Submit Button */}
            <TouchableOpacity
              style={styles.primaryButton}
              onPress={handleSubmit}
              disabled={submitting}
            >
              {submitting ? (
                <ActivityIndicator color={colors.textInverse} />
              ) : (
                <>
                  <Text style={styles.primaryButtonText}>Complete & Start Learning</Text>
                  <Icon name="check" size={18} color={colors.textInverse} />
                </>
              )}
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.backStepButton}
              onPress={() => setStep('TRACK')}
              disabled={submitting}
            >
              <Icon name="arrow-left" size={16} color={colors.textSecondary} />
              <Text style={styles.backStepButtonText}>Back to Track Selection</Text>
            </TouchableOpacity>
          </ScrollView>
        </KeyboardAvoidingView>
      )}

      {/* DROPDOWN SELECTION MODAL WITH SEARCH */}
      {activeDropdownField && (
        <Modal
          visible={!!activeDropdownField}
          transparent={true}
          animationType="slide"
          onRequestClose={() => setActiveDropdownField(null)}
        >
          <View style={styles.modalOverlay}>
            <View style={styles.modalContent}>
              <View style={styles.modalHeader}>
                <Text style={styles.modalTitle}>Select {activeDropdownField.label}</Text>
                <TouchableOpacity
                  onPress={() => setActiveDropdownField(null)}
                  style={styles.modalCloseIcon}
                >
                  <Icon name="x" size={20} color={colors.textSecondary} />
                </TouchableOpacity>
              </View>

              {/* Quick Search for long option lists like States */}
              {activeDropdownField.options && activeDropdownField.options.length > 8 && (
                <View style={styles.searchBox}>
                  <Icon name="search" size={16} color={colors.textSecondary} />
                  <TextInput
                    style={styles.searchInput}
                    placeholder="Search options..."
                    placeholderTextColor="#666"
                    value={dropdownSearch}
                    onChangeText={setDropdownSearch}
                    autoCorrect={false}
                  />
                  {dropdownSearch.length > 0 && (
                    <TouchableOpacity onPress={() => setDropdownSearch('')}>
                      <Icon name="x" size={14} color={colors.textSecondary} />
                    </TouchableOpacity>
                  )}
                </View>
              )}

              <ScrollView style={styles.modalList} showsVerticalScrollIndicator={false}>
                {filteredOptions.length === 0 ? (
                  <Text style={styles.noOptionsText}>No matching options</Text>
                ) : (
                  filteredOptions.map((opt: string) => {
                    const isSelected = formData[activeDropdownField.name] === opt;
                    return (
                      <TouchableOpacity
                        key={opt}
                        style={[styles.optionItem, isSelected && styles.optionItemActive]}
                        onPress={() => {
                          handleInputChange(activeDropdownField.name, opt);
                          setActiveDropdownField(null);
                        }}
                      >
                        <Text style={[styles.optionText, isSelected && styles.optionTextActive]}>
                          {opt}
                        </Text>
                        {isSelected && <Icon name="check" size={16} color={colors.accent} />}
                      </TouchableOpacity>
                    );
                  })
                )}
              </ScrollView>
            </View>
          </View>
        </Modal>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  keyboardAvoidingView: {
    flex: 1,
  },
  centered: {
    justifyContent: 'center',
    alignItems: 'center',
  },
  header: {
    paddingHorizontal: 20,
    paddingTop: 16,
    paddingBottom: 16,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  stepIndicatorRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 10,
  },
  stepDot: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: '#27272a',
    justifyContent: 'center',
    alignItems: 'center',
  },
  stepDotActive: {
    backgroundColor: colors.accent,
  },
  stepDotText: {
    fontSize: 12,
    fontWeight: 'bold',
    color: '#000',
  },
  stepDotTextInactive: {
    color: '#a1a1aa',
  },
  stepLine: {
    width: 32,
    height: 2,
    backgroundColor: '#27272a',
    marginHorizontal: 8,
  },
  stepLineActive: {
    backgroundColor: colors.accent,
  },
  headerStepText: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 1,
    marginBottom: 4,
  },
  headerTitle: {
    color: colors.text,
    fontSize: 24,
    fontWeight: 'bold',
  },
  headerSubtitle: {
    color: colors.textSecondary,
    fontSize: 13,
    marginTop: 4,
    lineHeight: 18,
  },
  trackScrollContainer: {
    padding: 20,
    paddingBottom: 40,
  },
  trackCard: {
    backgroundColor: '#111113',
    borderRadius: radius.lg,
    borderWidth: 1.5,
    borderColor: '#27272a',
    padding: 18,
    marginBottom: 18,
  },
  trackCardActive: {
    borderColor: colors.accent,
    backgroundColor: '#16140b',
  },
  trackCardTop: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 12,
  },
  trackIconWrap: {
    width: 48,
    height: 48,
    borderRadius: 12,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 14,
  },
  trackIconGold: {
    backgroundColor: 'rgba(245, 197, 24, 0.12)',
  },
  trackIconPurple: {
    backgroundColor: 'rgba(167, 139, 250, 0.12)',
  },
  trackHeaderInfo: {
    flex: 1,
  },
  badgeRow: {
    flexDirection: 'row',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 8,
  },
  trackTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: 'bold',
  },
  proBadge: {
    backgroundColor: 'rgba(245, 197, 24, 0.18)',
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  proBadgeText: {
    color: colors.accent,
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 0.5,
  },
  regularBadge: {
    backgroundColor: 'rgba(167, 139, 250, 0.18)',
  },
  regularBadgeText: {
    color: '#A78BFA',
  },
  trackTagline: {
    color: colors.textSecondary,
    fontSize: 12,
    marginTop: 2,
  },
  radioCircle: {
    width: 22,
    height: 22,
    borderRadius: 11,
    borderWidth: 2,
    borderColor: '#3f3f46',
    justifyContent: 'center',
    alignItems: 'center',
    marginLeft: 8,
  },
  radioCircleActive: {
    borderColor: colors.accent,
  },
  radioInner: {
    width: 12,
    height: 12,
    borderRadius: 6,
    backgroundColor: colors.accent,
  },
  trackDesc: {
    color: '#d4d4d8',
    fontSize: 13,
    lineHeight: 19,
    marginBottom: 14,
  },
  highlightsContainer: {
    backgroundColor: '#09090b',
    borderRadius: radius.md,
    padding: 12,
    gap: 8,
  },
  highlightRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  highlightText: {
    color: colors.textSecondary,
    fontSize: 12,
    flex: 1,
  },
  primaryButton: {
    backgroundColor: colors.accent,
    borderRadius: radius.md,
    paddingVertical: 16,
    paddingHorizontal: 20,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    marginTop: 12,
  },
  primaryButtonText: {
    color: colors.textInverse,
    fontSize: 16,
    fontWeight: 'bold',
  },
  buttonDisabled: {
    opacity: 0.4,
  },
  formContainer: {
    padding: 20,
  },
  selectedTrackBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#18181b',
    borderRadius: radius.md,
    paddingHorizontal: 14,
    paddingVertical: 12,
    marginBottom: 20,
    borderWidth: 1,
    borderColor: '#27272a',
  },
  bannerLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    flex: 1,
  },
  bannerText: {
    color: colors.textSecondary,
    fontSize: 13,
  },
  bannerTextBold: {
    color: colors.text,
    fontWeight: '700',
  },
  switchTrackBtn: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 6,
    backgroundColor: '#27272a',
  },
  switchTrackBtnText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: '600',
  },
  inputGroup: {
    marginBottom: 18,
  },
  label: {
    color: colors.text,
    fontSize: 14,
    marginBottom: 6,
    fontWeight: '600',
  },
  input: {
    backgroundColor: '#141416',
    color: colors.text,
    borderRadius: radius.md,
    paddingHorizontal: 14,
    paddingVertical: 13,
    fontSize: 15,
    borderWidth: 1,
    borderColor: '#27272a',
  },
  textarea: {
    height: 80,
    paddingTop: 12,
  },
  dropdownButton: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: '#141416',
    borderRadius: radius.md,
    paddingHorizontal: 14,
    paddingVertical: 14,
    borderWidth: 1,
    borderColor: '#27272a',
  },
  dropdownButtonText: {
    color: colors.text,
    fontSize: 15,
    flex: 1,
  },
  dropdownButtonPlaceholder: {
    color: '#666',
    fontSize: 15,
  },
  checkboxRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 4,
  },
  checkboxBox: {
    width: 22,
    height: 22,
    borderRadius: 6,
    borderWidth: 1.5,
    borderColor: '#3f3f46',
    backgroundColor: '#141416',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 10,
  },
  checkboxBoxChecked: {
    backgroundColor: colors.accent,
    borderColor: colors.accent,
  },
  checkboxLabel: {
    color: '#e4e4e7',
    fontSize: 14,
  },
  backStepButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 14,
    marginTop: 8,
  },
  backStepButtonText: {
    color: colors.textSecondary,
    fontSize: 14,
    fontWeight: '500',
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.85)',
    justifyContent: 'flex-end',
  },
  modalContent: {
    backgroundColor: '#18181b',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    maxHeight: '80%',
    padding: 20,
    borderWidth: 1,
    borderColor: '#27272a',
  },
  modalHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 14,
  },
  modalTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: 'bold',
  },
  modalCloseIcon: {
    padding: 4,
  },
  searchBox: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#121214',
    borderRadius: radius.md,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderWidth: 1,
    borderColor: '#27272a',
    marginBottom: 12,
    gap: 8,
  },
  searchInput: {
    flex: 1,
    color: colors.text,
    fontSize: 14,
    padding: 0,
  },
  modalList: {
    maxHeight: 380,
  },
  optionItem: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 13,
    paddingHorizontal: 14,
    borderRadius: 8,
    marginBottom: 6,
    backgroundColor: '#202023',
  },
  optionItemActive: {
    backgroundColor: 'rgba(245, 197, 24, 0.12)',
    borderWidth: 1,
    borderColor: colors.accent,
  },
  optionText: {
    color: colors.text,
    fontSize: 15,
  },
  optionTextActive: {
    color: colors.accent,
    fontWeight: 'bold',
  },
  noOptionsText: {
    color: colors.textSecondary,
    fontSize: 14,
    textAlign: 'center',
    paddingVertical: 24,
  },
});
