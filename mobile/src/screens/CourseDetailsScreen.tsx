import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, Image, ActivityIndicator, SafeAreaView, ScrollView, TouchableOpacity, Linking, Alert } from 'react-native';
import client from '../api/client';

export default function CourseDetailsScreen({ route, navigation }: any) {
  const { courseId } = route.params;
  const [course, setCourse] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchCourseDetails = async () => {
      try {
        const res = await client.get(`courses/${courseId}/`);
        setCourse(res.data);
      } catch (err) {
        console.error(err);
        Alert.alert('Error', 'Could not load course details');
        navigation.goBack();
      } finally {
        setLoading(false);
      }
    };
    fetchCourseDetails();
  }, [courseId]);

  if (loading) {
    return (
      <View style={[styles.container, styles.centered]}>
        <ActivityIndicator color="#facc15" size="large" />
      </View>
    );
  }

  let thumbUrl = course.thumbnail || 'https://images.unsplash.com/photo-1514320291840-2e0a9bf2a9ae?q=80&w=1470&auto=format&fit=crop';
  if (thumbUrl.startsWith('/')) {
    thumbUrl = `https://academy-api.natyaarts.com${thumbUrl}`;
  }

  const handleBuyCourse = () => {
    // Open the web version for checkout to bypass Google Play Billing 30% fee
    const url = `https://academy.natyaarts.com/courses/${courseId}`;
    Linking.openURL(url).catch(err => console.error("Couldn't load page", err));
  };

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => navigation.goBack()} style={styles.backButton}>
          <Text style={styles.backButtonText}>← Back</Text>
        </TouchableOpacity>
        <Text style={styles.headerTitle} numberOfLines={1}>{course.title}</Text>
      </View>

      <ScrollView contentContainerStyle={styles.scrollContent}>
        <Image source={{ uri: thumbUrl }} style={styles.thumbnail} />
        
        <View style={styles.content}>
          <Text style={styles.title}>{course.title}</Text>
          <Text style={styles.price}>₹{course.price}</Text>
          
          <Text style={styles.sectionTitle}>About this course</Text>
          <Text style={styles.description}>{course.description}</Text>

          <Text style={styles.sectionTitle}>Curriculum</Text>
          {course.modules?.map((mod: any, index: number) => (
            <View key={mod.id} style={styles.moduleItem}>
              <Text style={styles.moduleNumber}>{index + 1}</Text>
              <View style={styles.moduleInfo}>
                <Text style={styles.moduleTitle}>{mod.title}</Text>
                <Text style={styles.moduleDescription} numberOfLines={2}>{mod.description}</Text>
              </View>
            </View>
          ))}
        </View>
      </ScrollView>

      <View style={styles.footer}>
        <TouchableOpacity style={styles.buyButton} onPress={handleBuyCourse}>
          <Text style={styles.buyButtonText}>Buy Course for ₹{course.price}</Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#050505' },
  centered: { justifyContent: 'center', alignItems: 'center' },
  header: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 15, borderBottomWidth: 1, borderBottomColor: '#27272a', backgroundColor: '#050505' },
  backButton: { padding: 8, marginRight: 8 },
  backButtonText: { color: '#facc15', fontSize: 16, fontWeight: 'bold' },
  headerTitle: { color: '#fff', fontSize: 18, fontWeight: 'bold', flex: 1 },
  
  scrollContent: { paddingBottom: 100 },
  thumbnail: { width: '100%', height: 250, resizeMode: 'cover' },
  content: { padding: 20 },
  title: { color: '#fff', fontSize: 28, fontWeight: 'bold', marginBottom: 8 },
  price: { color: '#facc15', fontSize: 24, fontWeight: 'bold', marginBottom: 24 },
  
  sectionTitle: { color: '#fff', fontSize: 20, fontWeight: 'bold', marginBottom: 12, marginTop: 16 },
  description: { color: '#a1a1aa', fontSize: 16, lineHeight: 24, marginBottom: 16 },

  moduleItem: { flexDirection: 'row', backgroundColor: '#18181b', padding: 16, borderRadius: 12, marginBottom: 12, borderWidth: 1, borderColor: '#27272a' },
  moduleNumber: { color: '#facc15', fontSize: 20, fontWeight: 'bold', width: 30 },
  moduleInfo: { flex: 1 },
  moduleTitle: { color: '#fff', fontSize: 16, fontWeight: 'bold', marginBottom: 4 },
  moduleDescription: { color: '#a1a1aa', fontSize: 14 },

  footer: { position: 'absolute', bottom: 0, left: 0, right: 0, padding: 16, backgroundColor: '#050505', borderTopWidth: 1, borderTopColor: '#27272a' },
  buyButton: { backgroundColor: '#facc15', padding: 16, borderRadius: 12, alignItems: 'center' },
  buyButtonText: { color: '#000', fontSize: 18, fontWeight: 'bold' },
});
