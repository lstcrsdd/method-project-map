/// A playback profile.
class Profile {
  final String id;
  Profile(this.id);
}

/// Loads profiles.
class ConfigStore {
  Profile load(String id) => Profile(id);
}
