/*
   Novelty face authentication with liveness detection using depth and IR camera
   Copyright (C) 2017-2018
   Tomasz Garbus, Dominik Klemba, Jan Ludziejewski, Łukasz Raszkiewicz

   This program is free software: you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation, either version 3 of the License, or
   (at your option) any later version.

   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.

   You should have received a copy of the GNU General Public License
   along with this program.  If not, see <https://www.gnu.org/licenses/>.
*/

#include <cmath>
#include <stdexcept>
#include <string>
#include <sys/stat.h>

#include <wx/bitmap.h>
#include <wx/button.h>
#include <wx/event.h>
#include <wx/image.h>
#include <wx/panel.h>
#include <wx/slider.h>
#include <wx/stattext.h>
#include <wx/wx.h>
#include <wx/wxprec.h>

#include <libfreenect2/libfreenect2.hpp>
#include <libfreenect2/registration.h>

#include "basic_types.hpp"
#include "libkinect.hpp"
#include "picture.hpp"
#include <random>

// Constants

char constexpr photos_directory[] = "../photos/";  // should end with '/'

enum {
   ID_MIN_D = 101,
   ID_MAX_D = 102,
   ID_MIN_D_TEXT = 103,
   ID_MAX_D_TEXT = 104,
   ID_DISPLAY_COLOR = 105,
   ID_DISPLAY_DEPTH = 106,
   ID_DISPLAY_IR = 107,
   ID_DISPLAY_EXP = 108,
   ID_PHOTOS_BTN = 109,
   ID_EXP_BTN = 110,
   ID_FPS_TEXT = 111,
   ID_FPS_BTN = 112,
   ID_USERID_TEXT = 113,
   ID_USERID_SET_BTN = 114,
   ID_USERID_RAND_BTN = 115
};

const size_t display_panel_width = 512;
const size_t display_panel_height = 424;
const uint32_t default_max_fps = 10;

wxDEFINE_EVENT(REFRESH_DISPLAY_EVENT, wxCommandEvent);

// Declarations

struct Point3d {
   float x, y, z;
};

class DisplayPanel : public wxPanel {
   wxStaticBitmap *m_picture = nullptr;

 public:
   DisplayPanel(wxPanel *parent, wxWindowID window_id, uint8_t *bitmap);

   void refresh_display(wxCommandEvent &event);

   uint8_t *bitmap;
};

class SettingsPanel : public wxPanel {
 public:
   explicit SettingsPanel(wxPanel *parent);

   void on_min_slider_change(wxCommandEvent &event);
   void on_max_slider_change(wxCommandEvent &event);
   void on_min_text_change(wxCommandEvent &event);
   void on_max_text_change(wxCommandEvent &event);
   void on_photos_button_click(wxCommandEvent &event);
   void on_exp_button_click(wxCommandEvent &event);
   void on_fps_button_click(wxCommandEvent &event);
   void on_userid_set_button_click(wxCommandEvent &event);
   void on_userid_random_button_click(wxCommandEvent &event);

   wxPanel *m_parent;
   wxSlider *m_min_d, *m_max_d;
   wxTextCtrl *m_min_d_text, *m_max_d_text, *m_fps_text, *m_userid_text;
   wxButton *m_photos_button, *m_exp_button, *m_fps_button, *m_userid_set_button, *m_userid_random_button;

   int max_fps = default_max_fps;
   std::string userid = "";
   bool taking_photos = false, showing_exp = false;
};

class MainWindow : public wxFrame {
   wxPanel *m_parent;

 public:
   explicit MainWindow(const wxString &title, uint8_t *color_bitmap, uint8_t *depth_bitmap, uint8_t *ir_bitmap,
         uint8_t *custom_bitmap);

   void on_window_close(wxCloseEvent &event);

   DisplayPanel *m_display_color, *m_display_depth, *m_display_ir, *m_display_exp;
   bool display_exp_clear = true;
   SettingsPanel *m_settings;
   Picture *picture;
   KinectDevice *kinect_device;

   std::chrono::time_point<std::chrono::system_clock> last_shown_color, last_shown_de_ir;
   Picture::DepthOrIrFrame *buffer_depth, *buffer_ir;
};

// Definitions

SettingsPanel::SettingsPanel(wxPanel *parent)
      : wxPanel(parent, -1, wxPoint(-1, -1), wxSize(-1, -1), wxBORDER_SUNKEN), m_parent(parent),
        m_min_d(new wxSlider(this, ID_MIN_D, 500, 0, 10000, wxPoint(80, 10), wxSize(980, 15))),
        m_max_d(new wxSlider(this, ID_MAX_D, 4500, 0, 10000, wxPoint(80, 40), wxSize(980, 15))),
        m_min_d_text(new wxTextCtrl(this, ID_MIN_D_TEXT, "500", wxPoint(10, 10), wxSize(60, 15))),
        m_max_d_text(new wxTextCtrl(this, ID_MAX_D_TEXT, "4500", wxPoint(10, 40), wxSize(60, 15))),
        m_photos_button(new wxButton(this, ID_PHOTOS_BTN, "Start photos", wxPoint(10, 70), wxSize(100, 50))),
        m_exp_button(new wxButton(this, ID_EXP_BTN, "Turn on exp.", wxPoint(120, 70), wxSize(100, 50))),
        m_fps_text(
              new wxTextCtrl(this, ID_FPS_TEXT, std::to_string(default_max_fps), wxPoint(230, 70), wxSize(100, 50))),
        m_fps_button(new wxButton(this, ID_FPS_BTN, "Set max. FPS", wxPoint(340, 70), wxSize(100, 50))),
        m_userid_text(new wxTextCtrl(this, ID_USERID_TEXT, "", wxPoint(450, 70), wxSize(100, 50))),
        m_userid_set_button(new wxButton(this, ID_USERID_SET_BTN, "Set ID", wxPoint(560, 70), wxSize(100, 50))),
        m_userid_random_button(
              new wxButton(this, ID_USERID_RAND_BTN, "Random && set", wxPoint(670, 70), wxSize(100, 50))) {
   m_min_d->Bind(wxEVT_SCROLL_CHANGED, &SettingsPanel::on_min_slider_change, this);
   m_min_d->Bind(wxEVT_SCROLL_THUMBTRACK, &SettingsPanel::on_min_slider_change, this);
   m_max_d->Bind(wxEVT_SCROLL_CHANGED, &SettingsPanel::on_max_slider_change, this);
   m_max_d->Bind(wxEVT_SCROLL_THUMBTRACK, &SettingsPanel::on_max_slider_change, this);
   m_min_d_text->Bind(wxEVT_TEXT, &SettingsPanel::on_min_text_change, this);
   m_max_d_text->Bind(wxEVT_TEXT, &SettingsPanel::on_max_text_change, this);
   m_photos_button->Bind(wxEVT_BUTTON, &SettingsPanel::on_photos_button_click, this);
   m_exp_button->Bind(wxEVT_BUTTON, &SettingsPanel::on_exp_button_click, this);
   m_fps_button->Bind(wxEVT_BUTTON, &SettingsPanel::on_fps_button_click, this);
   m_userid_set_button->Bind(wxEVT_BUTTON, &SettingsPanel::on_userid_set_button_click, this);
   m_userid_random_button->Bind(wxEVT_BUTTON, &SettingsPanel::on_userid_random_button_click, this);
}

void SettingsPanel::on_min_slider_change(wxCommandEvent &event) {
   if (m_max_d->GetValue() <= m_min_d->GetValue()) {
      m_max_d->SetValue(m_min_d->GetValue() + 1);
      m_max_d_text->Clear();
      m_max_d_text->WriteText(std::to_string(m_min_d->GetValue() + 1));
   }

   m_min_d_text->Clear();
   m_min_d_text->WriteText(std::to_string(m_min_d->GetValue()));
}

void SettingsPanel::on_max_slider_change(wxCommandEvent &event) {
   if (m_max_d->GetValue() <= m_min_d->GetValue()) {
      m_min_d->SetValue(m_max_d->GetValue() - 1);
      m_min_d_text->Clear();
      m_min_d_text->WriteText(std::to_string(m_max_d->GetValue() - 1));
   }

   m_max_d_text->Clear();
   m_max_d_text->WriteText(std::to_string(m_max_d->GetValue()));
}

void SettingsPanel::on_min_text_change(wxCommandEvent &event) {
   long min_d;
   if (!m_min_d_text->GetValue().ToLong(&min_d)) {
      return;
   }
   m_min_d->SetValue(static_cast<int>(min_d));

   if (m_max_d->GetValue() <= min_d) {
      m_max_d->SetValue(static_cast<int>(min_d) + 1);
      m_max_d_text->Clear();
      m_max_d_text->WriteText(std::to_string(min_d + 1));
   }
}

void SettingsPanel::on_max_text_change(wxCommandEvent &event) {
   long max_d;
   if (!m_max_d_text->GetValue().ToLong(&max_d)) {
      return;
   }
   m_max_d->SetValue(static_cast<int>(max_d));

   if (max_d <= m_min_d->GetValue()) {
      m_min_d->SetValue(static_cast<int>(max_d) - 1);
      m_min_d_text->Clear();
      m_min_d_text->WriteText(std::to_string(max_d - 1));
   }
}

void SettingsPanel::on_photos_button_click(wxCommandEvent &event) {
   taking_photos = !taking_photos;
   m_photos_button->SetLabel(taking_photos ? "Stop photos" : "Start photos");
}

void SettingsPanel::on_exp_button_click(wxCommandEvent &event) {
   showing_exp = !showing_exp;
   m_exp_button->SetLabel(showing_exp ? "Turn off exp." : "Turn on exp.");
}

void SettingsPanel::on_fps_button_click(wxCommandEvent &event) {
   long new_max_fps;
   if (!m_fps_text->GetValue().ToLong(&new_max_fps)) {
      return;
   }
   max_fps = static_cast<uint32_t>(new_max_fps);
}

void SettingsPanel::on_userid_set_button_click(wxCommandEvent &event) {
   struct stat sb {};
   stat((photos_directory + m_userid_text->GetValue()).c_str(), &sb);
   if (!S_ISDIR(sb.st_mode)) {
      mkdir((std::string(photos_directory) + m_userid_text->GetValue()).c_str(), 0775);
   }
   userid = m_userid_text->GetValue();
}

void SettingsPanel::on_userid_random_button_click(wxCommandEvent &event) {
   std::random_device rd;
   std::mt19937 gen(rd());
   std::uniform_int_distribution<> dis(100000, 999999);
   int new_id;
   struct stat sb {};
   do {
      new_id = dis(gen);
      sb = {};
      stat((photos_directory + std::to_string(new_id)).c_str(), &sb);
   } while (S_ISDIR(sb.st_mode));
   mkdir((photos_directory + std::to_string(new_id)).c_str(), 0775);
   m_userid_text->SetValue(std::to_string(new_id));
   userid = std::to_string(new_id);
}

DisplayPanel::DisplayPanel(wxPanel *parent, wxWindowID window_id, uint8_t *bitmap)
      : wxPanel(parent, window_id, wxPoint(0, 0), wxSize(display_panel_width, display_panel_height), wxBORDER_SUNKEN),
        bitmap(bitmap) {
   Bind(REFRESH_DISPLAY_EVENT, &DisplayPanel::refresh_display, this);
   wxPostEvent(this, wxCommandEvent(REFRESH_DISPLAY_EVENT));
}

void DisplayPanel::refresh_display(wxCommandEvent &event) {
   delete m_picture;
   m_picture = new wxStaticBitmap(this, wxID_ANY,
         wxBitmap(wxImage(display_panel_width, display_panel_height, bitmap, true)), wxDefaultPosition, wxDefaultSize);
}

MainWindow::MainWindow(
      const wxString &title, uint8_t *color_bitmap, uint8_t *depth_bitmap, uint8_t *ir_bitmap, uint8_t *custom_bitmap)
      : wxFrame(nullptr, wxID_ANY, title, wxDefaultPosition, wxSize(1500, 1050)),
        picture(new Picture(nullptr, nullptr, nullptr)), m_parent(new wxPanel(this, wxID_ANY)),
        m_display_color(new DisplayPanel(m_parent, ID_DISPLAY_COLOR, color_bitmap)),
        m_display_depth(new DisplayPanel(m_parent, ID_DISPLAY_DEPTH, depth_bitmap)),
        m_display_ir(new DisplayPanel(m_parent, ID_DISPLAY_IR, ir_bitmap)),
        m_display_exp(new DisplayPanel(m_parent, ID_DISPLAY_EXP, custom_bitmap)),
        m_settings(new SettingsPanel(m_parent)) {
   this->Bind(wxEVT_CLOSE_WINDOW, &MainWindow::on_window_close, this);

   auto hbox1 = new wxBoxSizer(wxHORIZONTAL), hbox2 = new wxBoxSizer(wxHORIZONTAL);
   hbox1->Add(m_display_color);
   hbox1->Add(m_display_depth);
   hbox2->Add(m_display_ir);
   hbox2->Add(m_display_exp);

   auto vbox = new wxBoxSizer(wxVERTICAL);
   vbox->Add(hbox1, 1, wxEXPAND | wxALL, 5);
   vbox->Add(hbox2, 1, wxEXPAND | wxALL, 5);
   vbox->Add(m_settings, 1, wxEXPAND | wxALL, 5);

   m_parent->SetSizer(vbox);
   Centre();
}

void MainWindow::on_window_close(wxCloseEvent &event) {
   kinect_device->close();
   event.Skip();
}

// Utils

std::pair<size_t, size_t> fit_to_size(size_t width, size_t height, size_t max_width, size_t max_height) {
   size_t new_height = max_width * height / width;
   if (new_height <= max_height) {
      return {max_width, new_height};
   } else {
      return {max_height * width / height, max_height};
   }
}

template <typename VectorT, typename ElementT = double>
ElementT euclidian_norm(VectorT const vector) {
   size_t length = 0;
   ElementT ret = 0;

   for (auto const &x : vector) {
      ret += x * x;
   }

   return std::sqrt(ret);
}

template <typename VectorT, typename ElementT = double>
ElementT vector_dot(VectorT const &v, VectorT const &w) {
   if (v.size() != w.size()) {
      throw std::invalid_argument("v.size != w.size // TODO explanation?");
   }

   ElementT ret = 0;
   for (size_t i = 0; i < v.size(); ++i) {
      ret += v[i] * w[i];
   }

   return ret;
}

double calculate_reflectiveness_for_surface(std::array<std::array<Point3d const, 3>, 3> const square) {
   /*std::cerr << "square" << std::endl;
   for(auto row : square) {
      for(auto x : row)
        std::cerr << "(" << x.x << ", " << x.y << ", " << x.z <<"),  ";
      std::cerr << std::endl;
   }*/

   std::array<double, 3> v1{
         {square[1][0].x - square[1][1].x, square[1][0].y - square[1][1].y, square[1][0].z - square[1][1].z}},
         w1{{square[1][2].x - square[1][1].x, square[1][2].y - square[1][1].y, square[1][2].z - square[1][1].z}};

   std::array<double, 3> v2{
         {square[0][1].x - square[1][1].x, square[0][1].y - square[1][1].y, square[0][1].z - square[1][1].z}},
         w2{{square[2][1].x - square[1][1].x, square[2][1].y - square[1][1].y, square[2][1].z - square[1][1].z}};

   double const cos1 = vector_dot(v1, w1) / (euclidian_norm(v1) * euclidian_norm(w1));
   double const cos2 = vector_dot(v2, w2) / (euclidian_norm(v2) * euclidian_norm(w2));

   if (cos1 != cos1 || cos2 != cos2) {
      return 2.0;
   }

   double const ang = (std::acos(cos1) + std::acos(cos2)) / 3.14;

   return ang < 0.25 ? 0.5 : 2 * ang;
}

std::string make_filename(
      int which_kinect, std::chrono::time_point<std::chrono::system_clock> time_point, std::string user_id) {
   auto time_point_as_time_t = std::chrono::system_clock::to_time_t(time_point);
   char current_time_char[100];
   std::strftime(current_time_char, sizeof(current_time_char), "%Y-%m-%d-%H-%M-%S", std::gmtime(&time_point_as_time_t));
   std::string current_time_string = current_time_char;

   current_time_string += '-';
   std::string milliseconds = std::to_string(
         std::chrono::duration_cast<std::chrono::milliseconds>(time_point.time_since_epoch()).count() % 1000);
   current_time_string += std::string(3 - milliseconds.length(), '0') + milliseconds;

   return photos_directory + user_id + "/" + current_time_string + "-kinect" + std::to_string(which_kinect);
}

// Kinect handling

class MyKinectDevice : public KinectDevice {
 public:
   explicit MyKinectDevice(int device_number) : KinectDevice(device_number) {}

   void frame_handler(Picture const &picture) const override;

   MainWindow *window = nullptr;
};

void MyKinectDevice::frame_handler(Picture const &picture) const {
   if (window == nullptr) {
      return;
   }

   if (picture.color_frame
         && picture.color_frame->time_received - window->last_shown_color
                  >= std::chrono::milliseconds(1000 / window->m_settings->max_fps)) {

      window->last_shown_color = std::chrono::system_clock::now();

      if (window->m_settings->taking_photos) {
         std::string filename =
               make_filename(which_kinect, picture.color_frame->time_received, window->m_settings->userid);
         picture.color_frame->save_to_file(filename + ".png");
      }

      delete window->picture->color_frame;
      window->picture->color_frame = new Picture::ColorFrame(*picture.color_frame);

      auto frame_size = fit_to_size(window->picture->color_frame->pixels->width,
            window->picture->color_frame->pixels->height, display_panel_width, display_panel_height);
      size_t frame_width = frame_size.first;
      size_t frame_height = frame_size.second;

      if (frame_width != window->picture->color_frame->pixels->width
            || frame_height != window->picture->color_frame->pixels->height) {
         window->picture->color_frame->resize(frame_width, frame_height);
      }

      for (size_t i = 0; i < frame_height; ++i) {
         for (size_t j = 0; j < frame_width; ++j) {
            window->m_display_color->bitmap[3 * (i * display_panel_width + j)] =
                  (*window->picture->color_frame->pixels)[i][j].red;
            window->m_display_color->bitmap[3 * (i * display_panel_width + j) + 1] =
                  (*window->picture->color_frame->pixels)[i][j].green;
            window->m_display_color->bitmap[3 * (i * display_panel_width + j) + 2] =
                  (*window->picture->color_frame->pixels)[i][j].blue;
         }
      }

      wxPostEvent(window->m_display_color, wxCommandEvent(REFRESH_DISPLAY_EVENT));
   }

   if (picture.depth_frame) {
      delete window->buffer_depth;
      window->buffer_depth = new Picture::DepthOrIrFrame(*picture.depth_frame);
   } else if (picture.ir_frame) {
      delete window->buffer_ir;
      window->buffer_ir = new Picture::DepthOrIrFrame(*picture.ir_frame);
   }

   Picture::DepthOrIrFrame *depth_frame = window->buffer_depth, *ir_frame = window->buffer_ir;

   if (!depth_frame || !ir_frame) {
      return;
   }

   if (std::min(depth_frame->time_received, ir_frame->time_received) - window->last_shown_de_ir
         < std::chrono::milliseconds(1000 / window->m_settings->max_fps)) {
      return;
   }

   if (depth_frame->time_received - ir_frame->time_received > std::chrono::milliseconds(5)
         || depth_frame->time_received - ir_frame->time_received < std::chrono::milliseconds(-5)) {
      return;
   }

   window->last_shown_de_ir = std::chrono::system_clock::now();

   if (!depth_frame->is_depth || ir_frame->is_depth) {
      throw std::runtime_error("Something went wrong with buffering frames.");
   }

   if (true) {
      if (window->m_settings->taking_photos) {
         std::string filename = make_filename(which_kinect, depth_frame->time_received, window->m_settings->userid);
         depth_frame->save_to_file(filename + ".depth");
      }

      delete window->picture->depth_frame;
      window->picture->depth_frame = new Picture::DepthOrIrFrame(*depth_frame);

      auto frame_size = fit_to_size(window->picture->depth_frame->pixels->width,
            window->picture->depth_frame->pixels->height, display_panel_width, display_panel_height);
      size_t frame_width = frame_size.first;
      size_t frame_height = frame_size.second;

      if (frame_width != window->picture->depth_frame->pixels->width
            || frame_height != window->picture->depth_frame->pixels->height) {
         window->picture->depth_frame->resize(frame_width, frame_height);
      }

      float min_depth = window->m_settings->m_min_d->GetValue(), max_depth = window->m_settings->m_max_d->GetValue();
      if (max_depth - min_depth < 1.0) {
         max_depth = min_depth + 1.0f;
      }

      auto int_pixels = new uint8_t[frame_width * frame_height];
      for (size_t i = 0; i < frame_width * frame_height; ++i) {
         int_pixels[i] = uint8_t(std::max(0.0,
               std::min(255.0 * (window->picture->depth_frame->pixels->data()[i] - min_depth) / (max_depth - min_depth),
                                                255.0)));
      }

      cv::Mat current_image(
            cv::Size(static_cast<int>(frame_width), static_cast<int>(frame_height)), CV_8UC1, int_pixels);
      cv::Mat destination_image(cv::Size(static_cast<int>(frame_width), static_cast<int>(frame_height)), CV_8UC3);
      cv::applyColorMap(current_image, destination_image, cv::COLORMAP_RAINBOW);

      for (size_t i = 0; i < frame_height; ++i) {
         for (size_t j = 0; j < frame_width; ++j) {
            auto pixel = destination_image.at<cv::Vec3b>(static_cast<int>(i), static_cast<int>(j));
            window->m_display_depth->bitmap[3 * (i * display_panel_width + j)] = pixel[2];
            window->m_display_depth->bitmap[3 * (i * display_panel_width + j) + 1] = pixel[1];
            window->m_display_depth->bitmap[3 * (i * display_panel_width + j) + 2] = pixel[0];
         }
      }

      delete[] int_pixels;

      wxPostEvent(window->m_display_depth, wxCommandEvent(REFRESH_DISPLAY_EVENT));
   }

   if (true) {
      if (window->m_settings->taking_photos) {
         std::string filename = make_filename(which_kinect, ir_frame->time_received, window->m_settings->userid);
         ir_frame->save_to_file(filename + ".ir");
      }

      delete window->picture->ir_frame;
      window->picture->ir_frame = new Picture::DepthOrIrFrame(*ir_frame);

      auto frame_size = fit_to_size(window->picture->ir_frame->pixels->width, window->picture->ir_frame->pixels->height,
            display_panel_width, display_panel_height);
      size_t frame_width = frame_size.first;
      size_t frame_height = frame_size.second;

      if (frame_width != window->picture->ir_frame->pixels->width
            || frame_height != window->picture->ir_frame->pixels->height) {
         window->picture->ir_frame->resize(frame_width, frame_height);
      }

      float max_value;
      if (which_kinect == 1) {
         max_value = 1024.0;
      } else {
         max_value = 65535.0;
      }

      for (size_t i = 0; i < frame_height; ++i) {
         for (size_t j = 0; j < frame_width; ++j) {
            auto pixel_value = static_cast<uint8_t>(255.0 * (*window->picture->ir_frame->pixels)[i][j] / max_value);
            window->m_display_ir->bitmap[3 * (i * display_panel_width + j)] = pixel_value;
            window->m_display_ir->bitmap[3 * (i * display_panel_width + j) + 1] = pixel_value;
            window->m_display_ir->bitmap[3 * (i * display_panel_width + j) + 2] = pixel_value;
         }
      }

      wxPostEvent(window->m_display_ir, wxCommandEvent(REFRESH_DISPLAY_EVENT));
   }

   if (!window->m_settings->showing_exp && !window->display_exp_clear) {
      for (size_t i = 0; i < display_panel_height; ++i) {
         for (size_t j = 0; j < display_panel_width; ++j) {
            for (size_t k = 0; k < 3; ++k) {
               window->m_display_exp->bitmap[3 * (i * display_panel_width + j) + k] = 0;
            }
         }
      }

      window->display_exp_clear = true;

      wxPostEvent(window->m_display_exp, wxCommandEvent(REFRESH_DISPLAY_EVENT));
   }

   if (window->m_settings->showing_exp && (depth_frame || ir_frame) && window->picture->depth_frame
         && window->picture->ir_frame
         && window->picture->depth_frame->pixels->width == window->picture->ir_frame->pixels->width
         && window->picture->depth_frame->pixels->height == window->picture->ir_frame->pixels->height) {
      auto frame_width = window->picture->depth_frame->pixels->width,
           frame_height = window->picture->depth_frame->pixels->height;

      Matrix<double> values(frame_height, frame_width);
      double max_value = 0.0;

      libfreenect2::Registration registration(
            freenect2_device->getIrCameraParams(), freenect2_device->getColorCameraParams());
      // TODO: need to double check the impact of undistortDepth on the depth frame.
      libfreenect2::Frame undistorted(frame_width, frame_height, 4);
      registration.undistortDepth(window->picture->depth_frame->freenect2_frame.get(), &undistorted);
      Matrix<Point3d> points(frame_height, frame_width);
      Matrix<double> distance(frame_height, frame_width);

      for (size_t i = 0; i < frame_height; ++i) {
         for (size_t j = 0; j < frame_width; ++j) {
            distance[i][j] = reinterpret_cast<float const *>(undistorted.data)[i * frame_width + j];
         }
      }

      for (size_t i = 0; i < frame_height; ++i) {
         for (size_t j = 0; j < frame_width; ++j) {
            registration.getPointXYZ(&undistorted, static_cast<int>(i), static_cast<int>(j), points[i][j].x,
                  points[i][j].y, points[i][j].z);
         }
      }

      for (size_t i = 0; i < frame_height; ++i) {
         for (size_t j = 0; j < frame_width; ++j) {
            values[i][j] = distance[i][j] * distance[i][j] * (*window->picture->ir_frame->pixels)[i][j];

            if (i > 0 && j > 0 && i + 1 < frame_height && j + 1 < frame_width) {
               double reflectiveness = calculate_reflectiveness_for_surface(
                     {{{points[i - 1][j - 1], points[i - 1][j], points[i - 1][j + 1]},
                           {points[i + 0][j - 1], points[i + 0][j], points[i + 0][j + 1]},
                           {points[i + 1][j - 1], points[i + 1][j], points[i + 1][j + 1]}}});
               values[i][j] /= reflectiveness;

               max_value = std::max(max_value, values[i][j]);
            }
         }
      }

      // std::cerr << max_value << '\n';
      // This is a constant because otherwise the display flickers depending on the actual max value.
      // Uncoment the cerr above if you need to determine a new one after changing how it's calculated.
      max_value = 2e10;

      for (size_t i = 0; i < frame_height; ++i) {
         for (size_t j = 0; j < frame_width; ++j) {
            auto pixel_value = static_cast<uint8_t>(std::min(255.0, 255.0 * values[i][j] / max_value));
            float min_red = 255.0f * static_cast<float>(window->m_settings->m_min_d->GetValue()) / 10000.0f;
            float max_red = 255.0f * static_cast<float>(window->m_settings->m_max_d->GetValue()) / 10000.0f;
            if (pixel_value >= min_red && pixel_value <= max_red) {
               window->m_display_exp->bitmap[3 * (i * display_panel_width + j)] = 255;
               window->m_display_exp->bitmap[3 * (i * display_panel_width + j) + 1] = 0;
               window->m_display_exp->bitmap[3 * (i * display_panel_width + j) + 2] = 0;
            } else {
               window->m_display_exp->bitmap[3 * (i * display_panel_width + j)] = pixel_value;
               window->m_display_exp->bitmap[3 * (i * display_panel_width + j) + 1] = pixel_value;
               window->m_display_exp->bitmap[3 * (i * display_panel_width + j) + 2] = pixel_value;
            }
         }
      }

      window->display_exp_clear = false;

      wxPostEvent(window->m_display_exp, wxCommandEvent(REFRESH_DISPLAY_EVENT));
   }
}

// Main

class AppMain : public wxApp {
 public:
   bool OnInit() override;
   MyKinectDevice *kinect_device = nullptr;
};

bool AppMain::OnInit() {
   auto *color_bitmap = new uint8_t[display_panel_width * display_panel_height * 3];
   auto *depth_bitmap = new uint8_t[display_panel_width * display_panel_height * 3];
   auto *ir_bitmap = new uint8_t[display_panel_width * display_panel_height * 3];
   auto *custom_bitmap = new uint8_t[display_panel_width * display_panel_height * 3];
   for (size_t i = 0; i < display_panel_width * display_panel_height * 3; ++i) {
      color_bitmap[i] = 0;
      depth_bitmap[i] = 0;
      ir_bitmap[i] = 0;
      custom_bitmap[i] = 0;
   }

   MainWindow *window =
         new MainWindow(wxT("Live Kinect display"), color_bitmap, depth_bitmap, ir_bitmap, custom_bitmap);
   window->kinect_device = kinect_device;
   window->Show(true);

   kinect_device->window = window;

   return true;
}

int main(int argc, char **argv) {
   auto kinect_device = new MyKinectDevice(0);
   bool use_color, use_depth, use_ir;
   if (kinect_device->which_kinect == 1) {
      use_color = false;
      use_depth = true;
      use_ir = true;
   } else {
      use_color = true;
      use_depth = true;
      use_ir = true;
   }
   kinect_device->start_streams(use_color, use_depth, use_ir);

   auto app = new AppMain();
   app->kinect_device = kinect_device;
   wxApp::SetInstance(app);
   return wxEntry(argc, argv);
}
