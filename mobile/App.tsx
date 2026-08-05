import React, { useEffect, useState } from 'react';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { StatusBar, ActivityIndicator, View } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import axios from 'axios';

import LoginScreen from './src/screens/LoginScreen';
import DashboardScreen from './src/screens/DashboardScreen';
import LearnScreen from './src/screens/LearnScreen';
import OnboardingScreen from './src/screens/OnboardingScreen';
import CatalogScreen from './src/screens/CatalogScreen';
import CourseDetailsScreen from './src/screens/CourseDetailsScreen';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';

const Stack = createNativeStackNavigator();
const Tab = createBottomTabNavigator();

function MainTabs() {
  return (
    <Tab.Navigator
      screenOptions={{
        headerShown: false,
        tabBarStyle: { backgroundColor: '#050505', borderTopColor: '#27272a' },
        tabBarActiveTintColor: '#facc15',
        tabBarInactiveTintColor: '#a1a1aa',
      }}
    >
      <Tab.Screen name="My Learning" component={DashboardScreen} />
      <Tab.Screen name="Catalog" component={CatalogScreen} />
    </Tab.Navigator>
  );
}

export default function App() {
  const [initialRoute, setInitialRoute] = useState<string | null>(null);

  useEffect(() => {
    const checkToken = async () => {
      const token = await AsyncStorage.getItem('access_token');
      if (token) {
        try {
          // Verify with backend if onboarded
          const res = await axios.get('https://academy-api.natyaarts.com/api/users/me/', {
            headers: { Authorization: `Bearer ${token}` }
          });
          if (res.data.is_onboarded) {
            setInitialRoute('MainTabs');
          } else {
            setInitialRoute('Onboarding');
          }
        } catch (err) {
          // If token expired or failed, just go to login
          setInitialRoute('Login');
        }
      } else {
        setInitialRoute('Login');
      }
    };
    checkToken();
  }, []);

  if (!initialRoute) {
    return (
      <View style={{ flex: 1, backgroundColor: '#050505', justifyContent: 'center', alignItems: 'center' }}>
        <ActivityIndicator color="#facc15" size="large" />
      </View>
    );
  }

  return (
    <>
      <StatusBar barStyle="light-content" backgroundColor="#050505" />
      <NavigationContainer>
        <Stack.Navigator initialRouteName={initialRoute} screenOptions={{ headerShown: false, contentStyle: { backgroundColor: '#050505' } }}>
          <Stack.Screen name="Login" component={LoginScreen} />
          <Stack.Screen name="Onboarding" component={OnboardingScreen} />
          <Stack.Screen name="MainTabs" component={MainTabs} />
          <Stack.Screen name="CourseDetails" component={CourseDetailsScreen} />
          <Stack.Screen name="Learn" component={LearnScreen} />
        </Stack.Navigator>
      </NavigationContainer>
    </>
  );
}
