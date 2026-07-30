from django.db import models

class HeroSection(models.Model):
    title = models.CharField(max_length=255, default="Mastering Indian Classical Arts.")
    subtitle = models.CharField(max_length=255, default="Welcome to Natya LMS")
    description = models.TextField(default="Premium pre-recorded masterclasses, multi-lingual AI dubbing, and structured learning for all.")
    button_text = models.CharField(max_length=100, default="Browse Masterclasses")
    button_link = models.CharField(max_length=255, default="/courses")
    bg_image_url = models.URLField(max_length=500, default="https://natyaarts.com/img/hero.png")

    def __str__(self):
        return "Hero Section Settings"

    class Meta:
        verbose_name_plural = "Hero Section"

class Feature(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField()
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return self.title
