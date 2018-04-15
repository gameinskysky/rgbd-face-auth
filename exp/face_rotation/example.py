""" A simple demo to demonstrate an example usage of module rotate. """


from common.db_helper import DBHelper
from face_rotation import rotate
from face_rotation.find_angle import find_angle
from face_rotation import trim_face


if __name__ == '__main__':
    def load_samples(database, limit=10):
        samples = []
        print('Loading database %s with limit %d' % (database.get_name(), limit))
        for i in range(database.subjects_count()):
            for j in range(database.imgs_per_subject(i)):
                if len(samples) >= limit:
                    return samples
                x = database.load_greyd_face(i, j)
                if x.grey_img is None or x.depth_img is None:
                    continue
                samples.append(x)
        return samples

    # Load a random photo to rotate
    helper = DBHelper()
    TOTAL_SUBJECTS_COUNT = helper.all_subjects_count()
    photos = []
    for database in helper.get_databases():
        if database.get_name() != 'www.vap.aau.dk':
            photos += load_samples(database, limit=6)

    for face in photos:

        # Display the original photo
        face.show_grey()
        face.show_depth()

        # Trim face
        trim_face.trim_greyd(face)
        img_grey, img_depth = face

        # Display trimmed photo
        face.show_grey()
        face.show_depth()

        # Drop corner values and rescale to 0...1
        rotate.drop_corner_values(face)

        # Display the photo after normalizing mean
        face.show_grey()
        face.show_depth()

        # TODO: delete when find_angle code below works
        continue

        # Find the angle
        rotation, face_points = find_angle(face)
        if rotation is None:
            continue
        center = face_points["forehead"]
        print("center = " + str(center))

        # Apply rotation
        rotated_face, face_points = rotate.rotate_greyd_img(face, rotation, face_points)

        rotated_face.show_grey()
        rotated_face.show_depth()
        #face_rotation.find_angle.show_with_landmarks_normalized(rotated_face.grey_img, face_points)

        # show_with_center(rotated_grey, center)
        # rotated_grey, rotated_depth = recentre(rotated_grey, rotated_depth, face_points["forehead"])
        # show_with_center(rotated_grey, (1/2, 1/5))

        # tools.show_3d_plot(rotate.to_one_matrix(rotated_face))
        # Display the results
        # tools.show_image(rotated_depth)
        # tools.show_image(rotated_grey)

        # exit(0)

