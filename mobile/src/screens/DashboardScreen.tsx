import React, { useEffect, useState, useCallback } from 'react';
import { View, Text, FlatList, TouchableOpacity, StyleSheet, Image, ActivityIndicator, SafeAreaView, RefreshControl } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import client from '../api/client';

export default function DashboardScreen({ navigation }: any) {
  const [enrolledCourses, setEnrolledCourses] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchDashboardData = async () => {
    try {
      const res = await client.get('courses/my_courses/');
      setEnrolledCourses(res.data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchDashboardData();
  }, []);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    fetchDashboardData();
  }, []);

  const handleLogout = async () => {
    await AsyncStorage.removeItem('access_token');
    await AsyncStorage.removeItem('refresh_token');
    navigation.replace('Login');
  };

  const renderEnrolledCourse = ({ item }: { item: any }) => {
    let thumbUrl = item.thumbnail || 'https://images.unsplash.com/photo-1514320291840-2e0a9bf2a9ae?q=80&w=1470&auto=format&fit=crop';
    if (thumbUrl.startsWith('/')) {
      thumbUrl = `https://academy-api.natyaarts.com${thumbUrl}`;
    }

    return (
      <TouchableOpacity 
        style={styles.courseCard} 
        onPress={() => navigation.navigate('Learn', { courseId: item.id || item.course_id, isEnrolled: true })}
      >
        <Image 
          source={{ uri: thumbUrl }} 
          style={styles.thumbnail} 
        />
        <View style={styles.cardContent}>
          <Text style={styles.courseTitle}>{item.title}</Text>
          <View style={styles.courseMetaContainer}>
            <Text style={styles.courseModules}>{item.modules?.length || 0} Modules</Text>
            <Text style={styles.enrolledBadge}>Enrolled</Text>
          </View>
        </View>
      </TouchableOpacity>
    );
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
        <Image source={require('../../assets/icon.png')} style={{ width: 40, height: 40, resizeMode: 'contain' }} />
        <TouchableOpacity onPress={handleLogout} style={styles.logoutButton}>
          <Text style={styles.logoutText}>Logout</Text>
        </TouchableOpacity>
      </View>
      
      <FlatList
        data={enrolledCourses}
        keyExtractor={(item) => (item.id || item.course_id).toString()}
        renderItem={renderEnrolledCourse}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#facc15" />}
        contentContainerStyle={styles.listContainer}
        ListHeaderComponent={
          <View style={styles.bannerContainer}>
            <Image 
              source={require('../../assets/banner.jpg')} 
              style={styles.bannerImage} 
            />
            <View style={styles.bannerOverlay}>
              <Text style={styles.bannerTitle}>My Learning</Text>
              <Text style={styles.bannerSubtitle}>Pick up right where you left off.</Text>
            </View>
          </View>
        }
        ListEmptyComponent={
          <View style={styles.emptyContainer}>
            <Text style={styles.emptyTitle}>No courses yet</Text>
            <Text style={styles.emptyText}>You haven't enrolled in any masterclasses yet. Explore our catalog on the website and begin your musical journey today.</Text>
          </View>
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#050505' },
  centered: { justifyContent: 'center', alignItems: 'center' },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: 20, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: '#27272a' },
  logoutButton: { padding: 8 },
  logoutText: { color: '#facc15', fontSize: 14 },
  
  // Banner Styles
  bannerContainer: { width: '100%', height: 220, position: 'relative', marginBottom: 24 },
  bannerImage: { width: '100%', height: '100%', resizeMode: 'cover' },
  bannerOverlay: { position: 'absolute', bottom: 0, left: 0, right: 0, padding: 20, backgroundColor: 'rgba(0,0,0,0.6)' },
  bannerTitle: { color: '#fff', fontSize: 28, fontWeight: 'bold' },
  bannerSubtitle: { color: '#a1a1aa', fontSize: 16, marginTop: 4 },

  listContainer: { paddingBottom: 24 },
  
  // Vertical List
  courseCard: { backgroundColor: '#0a0a0a', borderRadius: 16, overflow: 'hidden', marginBottom: 20, marginHorizontal: 16, borderWidth: 1, borderColor: '#27272a' },
  thumbnail: { width: '100%', height: 180, resizeMode: 'cover' },
  cardContent: { padding: 16 },
  courseTitle: { color: '#fff', fontSize: 20, fontWeight: 'bold', marginBottom: 12 },
  courseMetaContainer: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  courseModules: { color: '#a1a1aa', fontSize: 14, backgroundColor: '#18181b', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12 },
  enrolledBadge: { color: '#22c55e', fontSize: 14, fontWeight: 'bold' },
  
  // Empty State
  emptyContainer: { padding: 24, alignItems: 'center', marginTop: 20 },
  emptyTitle: { color: '#fff', fontSize: 24, fontWeight: 'bold', marginBottom: 12 },
  emptyText: { color: '#a1a1aa', fontSize: 16, textAlign: 'center', lineHeight: 24 },
});
